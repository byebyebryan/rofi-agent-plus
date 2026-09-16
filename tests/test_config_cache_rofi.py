from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from rofi_agent_plus import VERSION, app, engine
from rofi_agent_plus.cache import CACHE_VERSION, CacheStore, PresentationContext, build_snapshot
from rofi_agent_plus.config import ConfigError, PickerConfig, config_from_mapping, load_config
from rofi_agent_plus.contract_lifecycle import LifecycleError
from rofi_agent_plus.rofi import (
    AUTO_REFRESH_DATA_PREFIX,
    CHECK_NOTICE_DATA_PREFIX,
    ERROR_NOTICE_DATA_PREFIX,
    ERROR_NOTICE_SECONDS,
    FALLBACK_ICON_PATH,
    PROVIDER_ICON_PATHS,
    PROVIDER_LABELS,
    PROVIDER_SEARCH_TERMS,
    ROFI_DELIMITER_VALUE,
    ROFI_RECORD_SEPARATOR,
    ROFI_RETV_CUSTOM_1,
    ROFI_RETV_CUSTOM_2,
    ROFI_RETV_CUSTOM_3,
    ROFI_RETV_CUSTOM_6,
    ROFI_RETV_CUSTOM_19,
    ROW_SEPARATOR,
    NavigationState,
    _age,
    _background_command,
    _navigation_data,
    _parse_check_notice,
    _parse_error_notice,
    _parse_navigation_state,
    _parse_selection,
    _provider_icon,
    _refresh_data,
    parse_continuation_state,
    render_snapshot,
    run_rofi,
    selection_payload,
)

THREAD_ID = "00000000-0000-0000-0000-000000000001"
OPENCODE_ID = "ses_0319af718ffegy8N1IoMEggx4B"


def session(
    kind: str = "codex", identifier: str = THREAD_ID, **values: object
) -> dict[str, object]:
    result: dict[str, object] = {
        "contractMode": True,
        "backend": {
            "kind": "contract",
            "capability": "host-mesh-v1+tmux-session-v1",
            "meshRevision": None,
        },
        "kind": kind,
        "id": identifier,
        "name": "hello\nworld\x00",
        "cwd": str(Path.home() / "code/project"),
        "host": "workstation",
        "hostId": "workstation",
        "recencyAt": 100,
        "updatedAt": 100,
        "active": False,
        "activityState": "idle",
    }
    result.update(values)
    return result


def parse_row_options(row: str) -> tuple[str, dict[str, str]]:
    visible, separator, encoded = row.partition("\x00")
    if not separator:
        raise AssertionError("row has no option separator")
    fields = encoded.split("\x1f")
    if len(fields) % 2:
        raise AssertionError("row options are not key/value pairs")
    return visible, dict(zip(fields[::2], fields[1::2], strict=True))


def parse_rendered_records(output: str) -> tuple[list[str], list[str]]:
    delimiter_header = f"\x00delim\x1f{ROFI_DELIMITER_VALUE}\n"
    if delimiter_header in output:
        header_text, record_text = output.split(delimiter_header, 1)
        headers = [*header_text.split("\n"), delimiter_header.removesuffix("\n")]
        records = record_text.removesuffix(ROFI_RECORD_SEPARATOR).split(ROFI_RECORD_SEPARATOR)
    else:
        records = output.removesuffix(ROFI_RECORD_SEPARATOR).split(ROFI_RECORD_SEPARATOR)
        headers = [record for record in records if record.startswith("\x00")]
        records = [record for record in records if not record.startswith("\x00")]
    return headers, [record for record in records if record]


class ConfigTest(unittest.TestCase):
    def test_missing_config_has_only_provider_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = load_config(Path(temporary) / "missing.toml")
        self.assertEqual(40, config.max_sessions)
        self.assertEqual(30, config.refresh_seconds)

    def test_loads_provider_settings_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text("max_sessions = 12\nrefresh_seconds = 60\n")
            config = load_config(path)
        self.assertEqual(12, config.max_sessions)
        self.assertEqual(60, config.refresh_seconds)

    def test_rejects_unknown_keys_wrong_types_and_bounds(self) -> None:
        for text in (
            "mystery = true\n",
            'hosts = ["workstation"]\n',
            'host_routes = ["workstation=example"]\n',
            'aliases = ["workstation=workstation"]\n',
            'terminal = "ghostty"\n',
            "max_sessions = 0\n",
            "refresh_seconds = 301\n",
        ):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "config.toml"
                path.write_text(text)
                with self.assertRaises(ConfigError):
                    load_config(path)

    def test_cli_limit_overrides_config_without_host_or_ssh_options(self) -> None:
        config = config_from_mapping({"max_sessions": 12})
        args = app.build_parser().parse_args(["list", "--limit", "7"])
        merged = app._apply_cli_config(config, args)
        self.assertEqual(7, merged.max_sessions)

    def test_retired_cli_commands_and_companion_options_are_rejected(self) -> None:
        parser = app.build_parser()
        for argv in (
            ["open", "--id", THREAD_ID],
            ["open-claude", "--id", THREAD_ID],
            ["open-opencode", "--id", OPENCODE_ID],
            ["list", "--host", "host-a"],
            ["list", "--route", "host-a=example"],
            ["list", "--alias", "old=host-a"],
            ["list", "--no-local"],
            ["list", "--stream"],
            ["--timeout", "1", "list"],
            ["--ssh-connect-timeout", "1", "list"],
            ["refresh", "--terminal", "terminal"],
        ):
            with (
                self.subTest(argv=argv),
                self.assertRaises(SystemExit),
                mock.patch("sys.stderr", new=io.StringIO()),
            ):
                parser.parse_args(argv)

    def test_diagnostic_limit_uses_the_persisted_bound(self) -> None:
        args = app.build_parser().parse_args(["list", "--limit", "100"])
        self.assertEqual(100, app._apply_cli_config(PickerConfig(), args).max_sessions)
        args = app.build_parser().parse_args(["list", "--limit", "101"])
        with self.assertRaises(ConfigError):
            app._apply_cli_config(PickerConfig(), args)

    def test_fingerprint_excludes_ttl_but_tracks_session_limit(self) -> None:
        original = PickerConfig()
        self.assertEqual(
            original.fingerprint,
            original.with_overrides(refresh_seconds=60).fingerprint,
        )
        self.assertNotEqual(
            original.fingerprint, original.with_overrides(max_sessions=12).fingerprint
        )


class ProjectMetadataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def test_engine_package_and_project_versions_match(self) -> None:
        project = tomllib.loads((self.root / "pyproject.toml").read_text())
        self.assertEqual(engine.VERSION, project["project"]["version"])
        self.assertEqual(VERSION, engine.VERSION)
        self.assertEqual("0.4.1", engine.VERSION)
        self.assertIn(f"Version `{engine.VERSION}`", (self.root / "README.md").read_text())

    def test_ci_and_readme_describe_the_canonical_deployment_contract(self) -> None:
        readme = " ".join((self.root / "README.md").read_text().split())
        workflow = (self.root / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("config.toml", readme)
        self.assertIn("XDG_CACHE_HOME", readme)
        self.assertIn("Rofi script-mode", readme)
        self.assertIn("canonical implementation of Agent Plus", readme)
        self.assertIn("Current refresh/provider errors are shown for about three seconds", readme)
        self.assertIn("./scripts/check", workflow)

    def test_readme_documents_refresh_observation_semantics(self) -> None:
        readme = " ".join((self.root / "README.md").read_text().split())
        for phrase in (
            "Session recency and activity state are independent from observation confidence",
            "automatic stale-cache refresh or explicit `Alt+R` check",
            "current rows show `Checking` and retained rows show `Rechecking",
            "retained provider rows show `Last known · seen",
            "activity-only rows show `Activity seen · details unavailable`",
            "current provider data but secondary activity or Tmux failures show",
            "Cache age triggers a check but never, by itself, makes a row warning",
            "private v4 cache accepts a valid v3 snapshot in memory without rewriting it",
            "no resident process, push-update channel, or progressive row publication",
            "Successful completion shows a bounded `Checked just now` acknowledgement",
            "do not authorize selection or lifecycle actions",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, readme)
        self.assertNotIn("`Refreshing in background`", readme)

    def test_readme_keeps_tab_for_rows_and_left_right_for_views(self) -> None:
        readme = (self.root / "README.md").read_text()
        self.assertIn("-kb-custom-2 Right -kb-custom-3 Left", readme)
        self.assertIn("-kb-cancel Escape,Control+g", readme)
        self.assertIn("`Tab` and `Shift+Tab` use Rofi's normal row navigation", readme)
        self.assertNotIn("-kb-custom-4 Tab", readme)
        self.assertNotIn("-kb-custom-5 ISO_Left_Tab", readme)
        self.assertNotIn("-kb-custom-6 Escape", readme)
        self.assertNotIn("-kb-element-next", readme)
        self.assertNotIn("-kb-element-prev", readme)
        self.assertNotIn("compatibility aliases for next/previous view", readme)

    def test_runtime_dependencies_are_empty_and_optional_providers_are_documented(self) -> None:
        project = tomllib.loads((self.root / "pyproject.toml").read_text())
        self.assertEqual([], project["project"]["dependencies"])
        readme = (self.root / "README.md").read_text().lower()
        self.assertIn("claude code and opencode are optional", readme)
        self.assertIn("codex cli", readme)


class CacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = CacheStore(Path(self.temporary.name) / "cache")
        self.config = PickerConfig(max_sessions=40)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def events(*sessions: dict[str, object], errors: list[dict[str, str]] | None = None):
        yield {
            "event": "refresh-started",
            "hosts": ["local"],
            "hostCatalog": [{"hostId": "local", "display": "Local", "local": True}],
        }
        yield {
            "event": "host-complete",
            "host": "local",
            "sessions": list(sessions),
            "errors": errors or [],
        }
        yield {"event": "refresh-finished", "generatedAt": 100}

    def test_snapshot_is_versioned_private_and_atomic(self) -> None:
        snapshot = build_snapshot(self.config, self.events(session()), now=100)
        self.store.write(snapshot)
        self.assertEqual(CACHE_VERSION, self.store.load(self.config.fingerprint)["version"])
        self.assertEqual(0o700, stat.S_IMODE(self.store.root.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(self.store.snapshot_path.stat().st_mode))
        self.assertEqual([], list(self.store.root.glob(".snapshot.*.tmp")))

    def test_invalid_or_mismatched_cache_is_ignored(self) -> None:
        self.store.ensure_root()
        self.store.snapshot_path.write_text("not json")
        self.assertIsNone(self.store.load(self.config.fingerprint))
        self.store.write(
            {"version": CACHE_VERSION, "fingerprint": "other", "sessions": [], "hosts": {}}
        )
        self.assertIsNone(self.store.load(self.config.fingerprint))
        self.store.write(
            {
                "version": CACHE_VERSION,
                "fingerprint": self.config.fingerprint,
                "sessions": [],
                "hosts": {"local": "not-an-object"},
            }
        )
        self.assertIsNone(self.store.load(self.config.fingerprint))

    def test_cache_requires_a_valid_host_catalog(self) -> None:
        snapshot = build_snapshot(self.config, self.events(session()), now=100)
        for mutation in (
            lambda value: value.pop("hostCatalog"),
            lambda value: value.update(
                {"hostCatalog": [{"hostId": "local", "display": "Local", "local": False}]}
            ),
            lambda value: value.update(
                {
                    "hostCatalog": [
                        {"hostId": "local", "display": "Local", "local": True},
                        {"hostId": "local", "display": "Duplicate", "local": False},
                    ]
                }
            ),
            lambda value: value.update({"hostCatalog": []}),
            lambda value: value.update(
                {"hostCatalog": [{"hostId": "local", "display": "Local\u200b", "local": True}]}
            ),
        ):
            with self.subTest(mutation=mutation):
                candidate = json.loads(json.dumps(snapshot))
                mutation(candidate)
                self.store.write(candidate)
                self.assertIsNone(self.store.load(self.config.fingerprint))

    def test_missing_refresh_catalog_fails_closed_and_retains_compatible_catalog(self) -> None:
        previous = build_snapshot(self.config, self.events(session()), now=100)
        missing = list(self.events(session()))
        missing[0].pop("hostCatalog")
        current = build_snapshot(self.config, iter(missing), previous, now=200)
        self.assertEqual(previous["hostCatalog"], current["hostCatalog"])
        self.assertEqual(previous["generatedAt"], current["generatedAt"])
        self.assertIn("invalid refresh host catalog", json.dumps(current["errors"]))

    def test_selected_remote_refresh_retains_the_full_compatible_catalog(self) -> None:
        remote = session(host="Beta", hostId="beta", recencyAt=200)
        previous = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["local", "beta"],
                        "hostCatalog": [
                            {"hostId": "local", "display": "Local", "local": True},
                            {"hostId": "beta", "display": "Beta", "local": False},
                        ],
                    },
                    {"event": "host-complete", "host": "local", "sessions": [], "errors": []},
                    {
                        "event": "host-complete",
                        "host": "beta",
                        "sessions": [remote],
                        "errors": [],
                    },
                    {"event": "refresh-finished"},
                ]
            ),
            now=100,
        )
        current = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["beta"],
                        "hostCatalog": [
                            {"hostId": "local", "display": "Local", "local": True},
                            {"hostId": "beta", "display": "Beta", "local": False},
                        ],
                    },
                    {
                        "event": "host-complete",
                        "host": "beta",
                        "sessions": [remote],
                        "errors": [],
                    },
                    {"event": "refresh-finished"},
                ]
            ),
            previous,
            now=200,
            retain_unselected_hosts=True,
        )
        self.assertEqual(previous["hostCatalog"], current["hostCatalog"])
        self.assertEqual(previous["hosts"]["local"], current["hosts"]["local"])
        self.assertEqual(100, current["generatedAt"])
        self.assertEqual(previous["lastRefresh"], current["lastRefresh"])
        self.assertEqual(200, current["hosts"]["beta"]["observations"]["codex"]["lastSuccessAt"])

        aborted = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["beta"],
                        "hostCatalog": [
                            {"hostId": "local", "display": "Local", "local": True},
                            {"hostId": "beta", "display": "Beta", "local": False},
                        ],
                    }
                ]
            ),
            current,
            now=300,
            retain_unselected_hosts=True,
        )
        self.assertEqual(100, aborted["generatedAt"])
        self.assertEqual(previous["lastRefresh"], aborted["lastRefresh"])

    def test_partial_provider_failure_preserves_old_rows(self) -> None:
        old = session("codex", recencyAt=100)
        current = session("claude", THREAD_ID, recencyAt=200)
        previous = build_snapshot(self.config, self.events(old), now=100)
        current_snapshot = build_snapshot(
            self.config,
            self.events(
                current,
                errors=[{"host": "local", "stage": "threads", "message": "offline"}],
            ),
            previous,
            now=200,
        )
        self.assertEqual(
            {"codex", "claude"}, {item["kind"] for item in current_snapshot["sessions"]}
        )

    def test_v3_cache_upgrades_in_memory_without_a_cache_miss_or_read_rewrite(self) -> None:
        snapshot = build_snapshot(self.config, self.events(session()), now=100)
        legacy = json.loads(json.dumps(snapshot))
        legacy["version"] = 3
        legacy.pop("lastRefresh")
        for host in legacy["hosts"].values():
            host.pop("observations")
            for row in host["sessions"]:
                row.pop("sourceObservation")
        for row in legacy["sessions"]:
            row.pop("sourceObservation")
        self.store.ensure_root()
        self.store.snapshot_path.write_text(json.dumps(legacy))

        loaded = self.store.load(self.config.fingerprint)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(CACHE_VERSION, loaded["version"])
        self.assertEqual("complete", loaded["lastRefresh"]["outcome"])
        self.assertEqual("current", loaded["sessions"][0]["sourceObservation"])
        self.assertEqual(3, json.loads(self.store.snapshot_path.read_text())["version"])

        self.store.write(loaded)
        self.assertEqual(CACHE_VERSION, json.loads(self.store.snapshot_path.read_text())["version"])

    def test_v3_transaction_failure_keeps_retained_timestamp_but_normalizes_failed_attempt(
        self,
    ) -> None:
        snapshot = build_snapshot(self.config, self.events(session()), now=100)
        legacy = json.loads(json.dumps(snapshot))
        legacy["version"] = 3
        legacy.pop("lastRefresh")
        legacy["errors"].append(
            {"host": "local", "stage": "refresh", "message": "transaction aborted"}
        )
        for host in legacy["hosts"].values():
            host.pop("observations")
            for row in host["sessions"]:
                row.pop("sourceObservation")
        for row in legacy["sessions"]:
            row.pop("sourceObservation")
        self.store.ensure_root()
        self.store.snapshot_path.write_text(json.dumps(legacy))

        loaded = self.store.load(self.config.fingerprint)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(100, loaded["generatedAt"])
        self.assertEqual(
            {"attemptedAt": 0, "completedAt": None, "outcome": "failed"},
            loaded["lastRefresh"],
        )
        on_disk = json.loads(self.store.snapshot_path.read_text())
        self.assertEqual(3, on_disk["version"])
        self.assertNotIn("lastRefresh", on_disk)

    def test_v4_observation_metadata_fails_closed_when_malformed(self) -> None:
        snapshot = build_snapshot(self.config, self.events(session()), now=100)
        mutations = (
            lambda value: value["lastRefresh"].update({"outcome": "unknown"}),
            lambda value: value["lastRefresh"].update({"completedAt": None}),
            lambda value: value["lastRefresh"].update({"outcome": "failed"}),
            lambda value: value["hosts"]["local"]["observations"]["codex"].update(
                {"lastSuccessAt": "not-a-time"}
            ),
            lambda value: value["hosts"]["local"]["sessions"][0].update(
                {"sourceObservation": "untrusted"}
            ),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                candidate = json.loads(json.dumps(snapshot))
                mutate(candidate)
                self.store.write(candidate)
                self.assertIsNone(self.store.load(self.config.fingerprint))

    def test_observations_classify_complete_partial_and_failed_all_host_attempts(self) -> None:
        complete = build_snapshot(self.config, self.events(session()), now=100)
        self.assertEqual(
            {"attemptedAt": 100, "completedAt": 100, "outcome": "complete"},
            complete["lastRefresh"],
        )
        self.assertTrue(
            all(
                stage == {"lastAttemptAt": 100, "lastSuccessAt": 100, "outcome": "ok"}
                for stage in complete["hosts"]["local"]["observations"].values()
            )
        )

        partial = build_snapshot(
            self.config,
            self.events(
                session(),
                errors=[{"host": "local", "stage": "threads", "message": "offline"}],
            ),
            complete,
            now=200,
        )
        self.assertEqual("partial", partial["lastRefresh"]["outcome"])
        self.assertEqual(
            {"lastAttemptAt": 200, "lastSuccessAt": 100, "outcome": "failed"},
            partial["hosts"]["local"]["observations"]["codex"],
        )
        self.assertEqual(
            {"lastAttemptAt": 200, "lastSuccessAt": 200, "outcome": "ok"},
            partial["hosts"]["local"]["observations"]["claude"],
        )

        aborted = build_snapshot(
            self.config,
            iter(
                [
                    {
                        "event": "refresh-started",
                        "hosts": ["local"],
                        "hostCatalog": [{"hostId": "local", "display": "Local", "local": True}],
                    }
                ]
            ),
            partial,
            now=300,
        )
        self.assertEqual(200, aborted["generatedAt"])
        self.assertEqual(
            {"attemptedAt": 300, "completedAt": None, "outcome": "failed"},
            aborted["lastRefresh"],
        )

    def test_source_observation_preserves_provider_retention_and_activity_only_rows(self) -> None:
        previous = build_snapshot(self.config, self.events(session(name="provider")), now=100)
        current = build_snapshot(
            self.config,
            self.events(
                session(
                    name=THREAD_ID[:8],
                    cwd="",
                    recencyAt=0,
                    updatedAt=0,
                    active=True,
                    activityState="active",
                    sourceObservation="activity-only",
                ),
                errors=[{"host": "local", "stage": "threads", "message": "offline"}],
            ),
            previous,
            now=200,
        )
        retained = current["sessions"][0]
        self.assertEqual("provider", retained["name"])
        self.assertEqual("retained", retained["sourceObservation"])
        self.assertTrue(retained["active"])

        activity_only = build_snapshot(
            self.config,
            self.events(
                session(
                    "claude",
                    "00000000-0000-0000-0000-000000000002",
                    cwd="",
                    recencyAt=0,
                    updatedAt=0,
                    active=True,
                    activityState="active",
                    sourceObservation="activity-only",
                )
            ),
            now=300,
        )
        self.assertEqual("activity-only", activity_only["sessions"][0]["sourceObservation"])
        self.assertNotIn(
            "sourceObservation", json.loads(selection_payload(activity_only["sessions"][0]))
        )

    def test_stage_classification_keeps_tmux_missing_authoritative_and_diagnostics_nonfatal(
        self,
    ) -> None:
        snapshot = build_snapshot(
            self.config,
            self.events(
                session(),
                errors=[
                    {"host": "local", "stage": "threads", "message": "offline"},
                    {"host": "local", "stage": "active", "message": "offline"},
                    {"host": "local", "stage": "tmux-missing", "message": "reached"},
                    {"host": "local", "stage": "tmux-correlation", "message": "ambiguous"},
                    {"host": "local", "stage": "route-health", "message": "hint failed"},
                ],
            ),
            now=100,
        )
        observations = snapshot["hosts"]["local"]["observations"]
        self.assertEqual("failed", observations["codex"]["outcome"])
        self.assertEqual("failed", observations["activity"]["outcome"])
        self.assertEqual("ok", observations["tmux"]["outcome"])
        self.assertEqual("ok", observations["claude"]["outcome"])
        self.assertEqual("ok", observations["opencode"]["outcome"])
        self.assertEqual("partial", snapshot["lastRefresh"]["outcome"])

        tmux_failed = build_snapshot(
            self.config,
            self.events(
                session(),
                errors=[{"host": "local", "stage": "tmux", "message": "offline"}],
            ),
            snapshot,
            now=200,
        )
        self.assertEqual(
            {"lastAttemptAt": 200, "lastSuccessAt": 100, "outcome": "failed"},
            tmux_failed["hosts"]["local"]["observations"]["tmux"],
        )

        diagnostic_only = build_snapshot(
            self.config,
            self.events(
                session(),
                errors=[{"host": "local", "stage": "route-health", "message": "hint failed"}],
            ),
            now=300,
        )
        self.assertEqual("complete", diagnostic_only["lastRefresh"]["outcome"])
        self.assertTrue(
            all(
                stage["outcome"] == "ok"
                for stage in diagnostic_only["hosts"]["local"]["observations"].values()
            )
        )

    def test_fresh_cache_skips_discovery_and_stale_cache_refreshes(self) -> None:
        calls: list[int] = []

        def discover(_config: PickerConfig, _previous: object):
            calls.append(1)
            yield from self.events(session())

        first = self.store.refresh(self.config, discover=discover)
        self.assertEqual(1, len(calls))
        second = self.store.refresh(self.config, discover=discover)
        self.assertEqual(1, len(calls))
        self.assertEqual(first["generatedAt"], second["generatedAt"])
        stale = dict(second)
        stale["generatedAt"] = int(time.time()) - 100
        self.store.write(stale)
        self.store.refresh(self.config, discover=discover)
        self.assertEqual(2, len(calls))

    def test_lock_deduplicates_nonblocking_refresh(self) -> None:
        with self.store.lock() as acquired:
            self.assertTrue(acquired)
            with self.store.lock(blocking=False) as second:
                self.assertFalse(second)

    def test_background_refresh_marker_deduplicates_and_is_private(self) -> None:
        command = [sys.executable, "-c", "pass"]
        callback_environment = {
            "ROFI_DATA": "state",
            "ROFI_INFO": "selection",
            "ROFI_INPUT": "filter",
            "ROFI_OUTSIDE": "123",
            "ROFI_RETV": "0",
        }
        with (
            mock.patch.dict(os.environ, {**callback_environment, "PICKER_TEST_VALUE": "kept"}),
            mock.patch("rofi_agent_plus.cache.subprocess.Popen") as popen,
        ):
            self.assertTrue(self.store.spawn_background(command))
            self.assertFalse(self.store.spawn_background(command))
            popen.assert_called_once()
        child_environment = popen.call_args.kwargs["env"]
        self.assertEqual("kept", child_environment["PICKER_TEST_VALUE"])
        for key in callback_environment:
            self.assertNotIn(key, child_environment)
        self.assertEqual(0o600, stat.S_IMODE(self.store.background_path.stat().st_mode))
        self.store.clear_background_marker()

    def test_background_marker_query_reports_age_and_activity(self) -> None:
        self.store.ensure_root()
        self.store.background_path.write_text("worker")
        modified = self.store.background_path.stat().st_mtime
        self.assertAlmostEqual(2.0, self.store.background_age(now=modified + 2.0))
        self.assertTrue(self.store.background_active(max_age=2.0, now=modified + 2.0))
        self.assertFalse(self.store.background_active(max_age=1.0, now=modified + 2.0))
        self.store.clear_background_marker()

    def test_activity_failure_keeps_last_known_activity(self) -> None:
        old = session(active=True, activityState="active")
        previous = build_snapshot(self.config, self.events(old), now=100)
        current = build_snapshot(
            self.config,
            self.events(
                session(active=False, activityState="unknown"),
                errors=[{"host": "local", "stage": "active", "message": "offline"}],
            ),
            previous,
            now=200,
        )
        self.assertTrue(current["sessions"][0]["active"])
        self.assertNotIn("tmux", current["sessions"][0])
        self.assertEqual("current", current["sessions"][0]["sourceObservation"])
        self.assertEqual(
            {"lastAttemptAt": 200, "lastSuccessAt": 100, "outcome": "failed"},
            current["hosts"]["local"]["observations"]["activity"],
        )


class RofiProtocolTest(unittest.TestCase):
    def test_background_command_supports_checkout_and_installed_layouts(self) -> None:
        checkout = _background_command()
        self.assertEqual(sys.executable, checkout[0])
        self.assertTrue(checkout[1].endswith("/bin/rofi-agent-plus"))
        self.assertEqual(["refresh", "--background"], checkout[2:])

        with mock.patch("rofi_agent_plus.rofi.__file__", "/opt/venv/lib/rofi_agent_plus/rofi.py"):
            self.assertEqual(
                [sys.executable, "-m", "rofi_agent_plus", "refresh", "--background"],
                _background_command(),
            )

    def test_missing_or_invalid_timestamp_has_unknown_age(self) -> None:
        for timestamp in (None, 0, -1, "", "not-a-time"):
            with self.subTest(timestamp=timestamp):
                self.assertEqual("unknown", _age(timestamp, now=100))

    def test_rows_escape_protocol_controls_and_include_search_metadata(self) -> None:
        output = render_snapshot(
            {"sessions": [session(active=True, activityState="active")]}, now=100
        )
        self.assertIn("\x00prompt\x1fAgents", output)
        self.assertIn("\x00use-hot-keys\x1ftrue", output)
        self.assertIn("\x00markup-rows\x1ftrue", output)
        self.assertIn(f"\x00delim\x1f{ROFI_DELIMITER_VALUE}\n", output)
        self.assertNotIn("hello\nworld", output)
        _, rows = parse_rendered_records(output)
        row = next(record for record in rows if record.startswith("hello"))
        visible, options = parse_row_options(row)
        self.assertEqual(1, row.count("\x00"))
        self.assertIn("Codex", visible)
        self.assertEqual("true", options["active"])
        self.assertEqual(str(PROVIDER_ICON_PATHS["codex"]), options["icon"])
        display = options["display"]
        self.assertIn(f"<b>hello world</b>{ROW_SEPARATOR}", display)
        self.assertIn('<span size="smaller" alpha="75%">', display)
        self.assertIn("workstation  ·  ~/code/project  ·  0s  ·  active", display)
        decoded = json.loads(options["info"])
        self.assertEqual(THREAD_ID, decoded["id"])

    def test_observation_status_is_display_only_and_keeps_current_rows_plain(self) -> None:
        selected = session(
            active=True,
            activityState="active",
            recencyAt=200,
            sourceObservation="current",
        )
        snapshot = {
            "sessions": [selected],
            "hosts": {
                "workstation": {
                    "sessions": [selected],
                    "errors": [],
                    "observations": {
                        stage: {
                            "lastAttemptAt": 200,
                            "lastSuccessAt": 200,
                            "outcome": "ok",
                        }
                        for stage in ("codex", "claude", "opencode", "activity", "tmux")
                    },
                }
            },
            "lastRefresh": {
                "attemptedAt": 200,
                "completedAt": 200,
                "outcome": "complete",
            },
            "errors": [],
        }
        output = render_snapshot(snapshot, now=200)
        _, rows = parse_rendered_records(output)
        visible, options = parse_row_options(rows[0])
        self.assertNotIn("Checking", visible)
        self.assertNotIn("Last known", visible)
        self.assertNotIn("Checking", options["info"])
        self.assertNotIn("sourceObservation", options["info"])
        self.assertNotIn("urgent", options)
        self.assertEqual("true", options["active"])
        self.assertIn("workstation  ·  ~/code/project  ·  0s  ·  active", options["display"])

    def test_observation_statuses_distinguish_retained_global_failure_and_activity_only(
        self,
    ) -> None:
        def render(
            row: dict[str, object],
            *,
            outcome: str = "complete",
            activity_outcome: str = "ok",
        ) -> tuple[str, dict[str, str]]:
            snapshot = {
                "sessions": [row],
                "hosts": {
                    "workstation": {
                        "sessions": [row],
                        "errors": [],
                        "observations": {
                            stage: {
                                "lastAttemptAt": 200,
                                "lastSuccessAt": 150,
                                "outcome": (activity_outcome if stage == "activity" else "ok"),
                            }
                            for stage in ("codex", "claude", "opencode", "activity", "tmux")
                        },
                    }
                },
                "lastRefresh": {
                    "attemptedAt": 200,
                    "completedAt": None if outcome == "failed" else 200,
                    "outcome": outcome,
                },
                "errors": [],
            }
            _, rows = parse_rendered_records(render_snapshot(snapshot, now=200))
            return parse_row_options(rows[0])

        visible, options = render(session(sourceObservation="retained", active=False))
        self.assertNotIn("Last known", visible)
        self.assertIn("◷ Last known · seen 50s", options["display"])
        self.assertEqual("true", options["urgent"])

        visible, options = render(
            session(sourceObservation="current", active=True), outcome="failed"
        )
        self.assertIn("◷ Last known · seen 50s", options["display"])
        self.assertEqual("true", options["urgent"])
        self.assertNotIn("active", options)
        self.assertNotIn("Last known", visible)

        visible, options = render(session(sourceObservation="activity-only", active=False))
        self.assertIn("Activity seen · details unavailable", options["display"])
        self.assertEqual("true", options["urgent"])

    def test_checking_and_rechecking_statuses_are_compact_and_truthful(self) -> None:
        current = session(sourceObservation="current")
        retained = session(
            identifier="00000000-0000-0000-0000-000000000002",
            name="retained",
            sourceObservation="retained",
        )
        snapshot = {
            "sessions": [current, retained],
            "hosts": {
                "workstation": {
                    "sessions": [current, retained],
                    "errors": [],
                    "observations": {
                        stage: {
                            "lastAttemptAt": 200,
                            "lastSuccessAt": 150,
                            "outcome": "ok",
                        }
                        for stage in ("codex", "claude", "opencode", "activity", "tmux")
                    },
                }
            },
            "lastRefresh": {
                "attemptedAt": 200,
                "completedAt": 200,
                "outcome": "complete",
            },
            "errors": [],
        }
        _, rows = parse_rendered_records(render_snapshot(snapshot, checking=True, now=200))
        rendered = {
            parse_row_options(row)[0].split("  ·  ")[0]: parse_row_options(row)[1] for row in rows
        }
        self.assertIn("◌ Checking", rendered["hello world"]["display"])
        self.assertIn("◌ Rechecking · last seen 50s", rendered["retained"]["display"])

        limited = session(sourceObservation="current", tmuxStale=True)
        snapshot["sessions"] = [limited]
        snapshot["hosts"]["workstation"]["sessions"] = [limited]
        _, rows = parse_rendered_records(render_snapshot(snapshot, checking=True, now=200))
        _, options = parse_row_options(rows[0])
        self.assertIn("◌ Checking · Details limited", options["display"])
        self.assertEqual("true", options["urgent"])

        activity_only = session(
            identifier="00000000-0000-0000-0000-000000000003",
            name="activity-only",
            sourceObservation="activity-only",
        )
        snapshot["sessions"] = [activity_only]
        snapshot["hosts"]["workstation"]["sessions"] = [activity_only]
        _, rows = parse_rendered_records(render_snapshot(snapshot, checking=True, now=200))
        _, options = parse_row_options(rows[0])
        self.assertIn(
            "◌ Checking · Activity seen · details unavailable",
            options["display"],
        )
        self.assertEqual("true", options["urgent"])

    def test_activity_failure_suppresses_active_but_missing_metadata_preserves_it(self) -> None:
        selected = session(active=True, activityState="active")
        snapshot = {
            "sessions": [selected],
            "hosts": {
                "workstation": {
                    "sessions": [selected],
                    "observations": {
                        "activity": {
                            "lastAttemptAt": 200,
                            "lastSuccessAt": 150,
                            "outcome": "failed",
                        }
                    },
                }
            },
            "lastRefresh": {
                "attemptedAt": 200,
                "completedAt": 200,
                "outcome": "partial",
            },
            "errors": [],
        }
        _, rows = parse_rendered_records(render_snapshot(snapshot, now=200))
        _, options = parse_row_options(rows[0])
        self.assertNotIn("active", options)
        self.assertEqual("true", options["urgent"])
        self.assertIn("Details limited", options["display"])

        output = render_snapshot({"sessions": [selected]}, now=200)
        _, rows = parse_rendered_records(output)
        _, options = parse_row_options(rows[0])
        self.assertEqual("true", options["active"])
        self.assertNotIn("urgent", options)

    def test_tmux_missing_and_route_health_do_not_make_rows_limited(self) -> None:
        selected = session(active=False, tmuxMissing=True)
        snapshot = {
            "sessions": [selected],
            "hosts": {
                "workstation": {
                    "sessions": [selected],
                    "errors": [
                        {"host": "workstation", "stage": "tmux-missing", "message": "none"},
                        {"host": "workstation", "stage": "route-health", "message": "hint"},
                    ],
                    "observations": {},
                }
            },
            "lastRefresh": {
                "attemptedAt": 200,
                "completedAt": 200,
                "outcome": "complete",
            },
            "errors": [],
        }
        _, rows = parse_rendered_records(render_snapshot(snapshot, now=200))
        _, options = parse_row_options(rows[0])
        self.assertNotIn("Details limited", options["display"])
        self.assertNotIn("urgent", options)

    def test_empty_success_is_not_urgent_but_unavailable_and_error_are(self) -> None:
        healthy = {
            "sessions": [],
            "lastRefresh": {"attemptedAt": 200, "completedAt": 200, "outcome": "complete"},
            "errors": [],
        }
        _, rows = parse_rendered_records(render_snapshot(healthy, now=200))
        _, options = parse_row_options(rows[0])
        self.assertEqual({"nonselectable": "true"}, options)

        failed = {
            "sessions": [],
            "lastRefresh": {"attemptedAt": 200, "completedAt": None, "outcome": "failed"},
            "errors": [],
        }
        _, rows = parse_rendered_records(render_snapshot(failed, now=200))
        _, options = parse_row_options(rows[0])
        self.assertEqual("true", options["urgent"])

        _, rows = parse_rendered_records(render_snapshot(None, now=200))
        _, options = parse_row_options(rows[0])
        self.assertEqual({"nonselectable": "true", "urgent": "true"}, options)

    def test_display_escapes_markup_and_hides_provider_while_filtering_keeps_it(self) -> None:
        selected = session(
            kind="claude",
            name="A < & >",
            host="host",
            cwd="/srv/project",
            recencyAt=100,
            activityState="waiting",
        )
        output = render_snapshot({"sessions": [selected]}, now=100)
        _, rows = parse_rendered_records(output)
        row = next(record for record in rows if record.startswith("A < & >"))
        visible, options = parse_row_options(row)
        display = options["display"]
        meta = options["meta"]
        self.assertIn("<b>A &lt; &amp; &gt;</b>", display)
        self.assertNotIn("Claude Code", display)
        self.assertIn("Claude Code", visible)
        self.assertIn("claude claude-code claude code", meta)
        self.assertIn("host  ·  /srv/project  ·  0s  ·  waiting", display)

    def test_each_provider_uses_its_icon_and_retains_search_terms(self) -> None:
        for kind in PROVIDER_LABELS:
            identifier = OPENCODE_ID if kind == "opencode" else THREAD_ID
            with self.subTest(kind=kind):
                output = render_snapshot(
                    {"sessions": [session(kind, identifier, activityState="active")]}, now=100
                )
                _, rows = parse_rendered_records(output)
                row = next(record for record in rows if record.startswith("hello"))
                visible, options = parse_row_options(row)
                display = options["display"]
                meta = options["meta"]
                self.assertEqual(str(PROVIDER_ICON_PATHS[kind]), options["icon"])
                self.assertIn(PROVIDER_LABELS[kind], visible)
                self.assertNotIn(PROVIDER_LABELS[kind], display)
                self.assertIn(PROVIDER_SEARCH_TERMS[kind], meta)

    def test_provider_icons_are_bundled_absolute_paths_with_safe_fallback(self) -> None:
        for kind, path in PROVIDER_ICON_PATHS.items():
            with self.subTest(kind=kind):
                self.assertTrue(path.is_absolute())
                self.assertTrue(path.is_file())
                self.assertEqual(str(path), _provider_icon(kind))
        self.assertTrue(FALLBACK_ICON_PATH.is_absolute())
        self.assertTrue(FALLBACK_ICON_PATH.is_file())
        self.assertEqual(str(FALLBACK_ICON_PATH), _provider_icon("unknown"))
        with mock.patch.dict(PROVIDER_ICON_PATHS, {"codex": Path("/missing/codex.svg")}):
            self.assertEqual(str(FALLBACK_ICON_PATH), _provider_icon("codex"))

    def test_provider_icon_assets_are_declared_as_package_data(self) -> None:
        root = Path(__file__).resolve().parents[1]
        project = tomllib.loads((root / "pyproject.toml").read_text())
        package_data = project["tool"]["setuptools"]["package-data"]["rofi_agent_plus"]
        self.assertIn("assets/providers/*.svg", package_data)
        self.assertEqual(
            {"claude.svg", "codex.svg", "generic.svg", "opencode.svg"},
            {path.name for path in (root / "rofi_agent_plus/assets/providers").glob("*.svg")},
        )

    def test_empty_snapshot_has_nonselectable_status_row(self) -> None:
        output = render_snapshot({"sessions": []}, message="No hosts reachable")
        _, rows = parse_rendered_records(output)
        row = next(record for record in rows if record.startswith("No sessions"))
        visible, options = parse_row_options(row)
        self.assertEqual("No sessions · No hosts reachable", visible)
        self.assertEqual({"nonselectable": "true", "urgent": "true"}, options)
        self.assertEqual(1, row.count("\x00"))

    def test_flat_scope_views_use_catalog_order_and_provider_icons(self) -> None:
        sessions = [
            session(
                name="zulu",
                host="zeta",
                hostId="zeta",
                recencyAt=300,
                active=True,
            ),
            session(
                "claude",
                "00000000-0000-0000-0000-000000000002",
                name="alpha",
                host="Alpha",
                hostId="alpha",
                recencyAt=300,
            ),
            session(
                "opencode",
                OPENCODE_ID,
                name="older",
                host="zeta",
                hostId="zeta",
                recencyAt=100,
            ),
        ]
        catalog = [
            {"hostId": "workstation", "display": "Workstation", "local": True},
            {"hostId": "zeta", "display": "Zeta", "local": False},
            {"hostId": "alpha", "display": "Alpha", "local": False},
        ]
        snapshot = {
            "sessions": sessions,
            "hostCatalog": catalog,
            "hosts": {
                "workstation": {"sessions": [], "errors": []},
                "zeta": {"sessions": [sessions[0], sessions[2]], "errors": []},
                "alpha": {"sessions": [sessions[1]], "errors": []},
            },
            "errors": [],
        }
        output = render_snapshot(snapshot, navigation=NavigationState(), now=300)
        _, rows = parse_rendered_records(output)
        parsed = [parse_row_options(row) for row in rows]
        self.assertEqual(
            ["alpha", "zulu", "older"],
            [visible.split("  ·  ")[0] for visible, _ in parsed],
        )
        self.assertEqual(str(PROVIDER_ICON_PATHS["claude"]), parsed[0][1]["icon"])
        self.assertNotIn("›", parsed[0][1]["display"])
        self.assertEqual("true", parsed[1][1]["active"])
        self.assertEqual("alpha", json.loads(parsed[0][1]["info"])["hostId"])

        output = render_snapshot(snapshot, navigation=NavigationState("local"), now=300)
        _, rows = parse_rendered_records(output)
        parsed = [parse_row_options(row) for row in rows]
        self.assertEqual(["No agent sessions on Local"], [visible for visible, _ in parsed])

        output = render_snapshot(snapshot, navigation=NavigationState("host", "alpha"), now=300)
        _, rows = parse_rendered_records(output)
        parsed = [parse_row_options(row) for row in rows]
        self.assertEqual(["alpha"], [visible.split("  ·  ")[0] for visible, _ in parsed])
        self.assertIn("Agents › Alpha", output)

        output = render_snapshot(
            {
                **snapshot,
                "hostCatalog": [*catalog, {"hostId": "empty", "display": "Empty", "local": False}],
            },
            navigation=NavigationState("host", "empty"),
        )
        _, rows = parse_rendered_records(output)
        visible, options = parse_row_options(rows[0])
        self.assertEqual("No agent sessions on Empty", visible)
        self.assertEqual("true", options["nonselectable"])

    def test_recent_and_nested_sessions_sort_valid_recency_then_identity(self) -> None:
        sessions = [
            session(name="unknown", recencyAt="not-a-time"),
            session(name="zulu", recencyAt=200),
            session(
                "codex",
                "00000000-0000-0000-0000-000000000003",
                name="alpha",
                recencyAt=200,
            ),
        ]
        output = render_snapshot({"sessions": sessions}, now=200)
        _, rows = parse_rendered_records(output)
        visible = [parse_row_options(row)[0] for row in rows]
        self.assertTrue(visible[0].startswith("alpha"))
        self.assertTrue(visible[1].startswith("zulu"))
        self.assertTrue(visible[2].startswith("unknown"))

    def test_navigation_state_round_trips_and_legacy_data_is_accepted(self) -> None:
        states = (
            NavigationState(),
            NavigationState("local"),
            NavigationState("host", "alpha"),
            NavigationState("host", "unsafe host › label"),
        )
        for state in states:
            with self.subTest(state=state):
                self.assertEqual(state, _parse_navigation_state(_navigation_data(state)))
        for legacy in (
            "idle",
            "background-refresh:123",
            "error-notice:124:offline",
            "background-refresh:123;error-notice:124:offline",
            "not-a-state",
        ):
            with self.subTest(legacy=legacy):
                self.assertEqual(NavigationState(), _parse_navigation_state(legacy))
        self.assertEqual(NavigationState(), _parse_navigation_state("navigation:not-json"))
        self.assertEqual(
            NavigationState(), _parse_navigation_state("navigation:%7B%22view%22%3A%5B%5D%7D")
        )

    def test_continuation_state_keeps_live_components_and_expires_them_independently(self) -> None:
        navigation = NavigationState("host", "workstation")
        data = _refresh_data(1010, 1003, "offline", navigation=navigation)
        state = parse_continuation_state(data)
        self.assertEqual(navigation, state.navigation)
        self.assertEqual(1010.0, state.refresh_deadline)
        self.assertEqual(1003.0, state.error_deadline)
        self.assertEqual("offline", state.error_message)
        self.assertEqual(state, state.active(now=1000))
        self.assertEqual(1010.0, state.active(now=1003).refresh_deadline)
        self.assertIsNone(state.active(now=1003).error_deadline)
        self.assertIsNone(state.active(now=1010).refresh_deadline)
        self.assertFalse(state.active(now=1010).has_lifecycle)

    def test_check_continuation_is_separate_and_expires_independently(self) -> None:
        navigation = NavigationState("host", "workstation")
        data = _refresh_data(1010, navigation=navigation, check_deadline=1002)
        state = parse_continuation_state(data)
        self.assertEqual(navigation, state.navigation)
        self.assertEqual(1010.0, state.refresh_deadline)
        self.assertEqual(1002.0, state.check_deadline)
        self.assertIsNone(state.error_deadline)
        self.assertEqual(state, state.active(now=1000))
        self.assertIsNone(state.active(now=1002).check_deadline)
        self.assertEqual(1010.0, state.active(now=1002).refresh_deadline)
        self.assertEqual(1002.0, _parse_check_notice(data))
        self.assertIn(CHECK_NOTICE_DATA_PREFIX, data)

        malformed = _parse_check_notice("check-notice:nope")
        self.assertIsNone(malformed)
        self.assertEqual(
            NavigationState(), parse_continuation_state("check-notice:nope").navigation
        )

    def test_config_error_custom19_clears_expired_notice_without_restarting_it(self) -> None:
        navigation = NavigationState()
        data = _refresh_data(None, 999, "old config error", navigation=navigation)
        output = io.StringIO()
        with (
            mock.patch("rofi_agent_plus.rofi.load_config", side_effect=ConfigError("config")),
            mock.patch("rofi_agent_plus.rofi.time.time", return_value=1000),
            mock.patch("sys.stdout", output),
        ):
            run_rofi(
                {"ROFI_RETV": str(ROFI_RETV_CUSTOM_19), "ROFI_DATA": data},
                store=mock.Mock(spec=CacheStore),
            )
        rendered = output.getvalue()
        self.assertNotIn("old config error", rendered)
        self.assertNotIn("\x00message\x1fconfig", rendered)
        self.assertNotIn("error-notice:", rendered)
        self.assertIn("Agents › All", rendered)
        self.assertIn(
            '\x00theme\x1fconfiguration { timeout { delay: 0; action: "kb-custom-19"; } }', rendered
        )

    def test_config_error_custom19_keeps_refresh_and_shows_new_error(self) -> None:
        navigation = NavigationState()
        data = _refresh_data(1010, navigation=navigation)
        output = io.StringIO()
        with (
            mock.patch(
                "rofi_agent_plus.rofi.load_config", side_effect=ConfigError("config offline")
            ),
            mock.patch("rofi_agent_plus.rofi.time.time", return_value=1000),
            mock.patch("sys.stdout", output),
        ):
            run_rofi(
                {"ROFI_RETV": str(ROFI_RETV_CUSTOM_19), "ROFI_DATA": data},
                store=mock.Mock(spec=CacheStore),
            )
        rendered = output.getvalue()
        self.assertIn("config offline", rendered)
        self.assertIn("background-refresh:1010;error-notice:", rendered)
        self.assertIn("Agents › All", rendered)
        self.assertIn(
            '\x00theme\x1fconfiguration { timeout { delay: 1; action: "kb-custom-19"; } }',
            rendered,
        )

    def test_navigation_callbacks_are_cache_only_and_preserve_filter_only(self) -> None:
        snapshot = {
            "sessions": [session()],
            "hostCatalog": [
                {"hostId": "workstation", "display": "Workstation", "local": True},
                {"hostId": "alpha", "display": "Alpha", "local": False},
            ],
            "hosts": {"workstation": {"sessions": [session()], "errors": []}},
            "errors": [],
        }
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = snapshot
        notice = "Refresh errors: local/threads: offline"

        def invoke(retv: int, state: NavigationState, info: str | None = None) -> str:
            output = io.StringIO()
            with (
                mock.patch("sys.stdout", output),
                mock.patch("rofi_agent_plus.rofi.time.time", return_value=1000),
            ):
                run_rofi(
                    {
                        "ROFI_RETV": str(retv),
                        "ROFI_DATA": _refresh_data(1010, 1003, notice, navigation=state),
                        **({"ROFI_INFO": info} if info is not None else {}),
                    },
                    store=store,
                    config=self._config(),
                )
            return output.getvalue()

        root = NavigationState()
        cycled = invoke(ROFI_RETV_CUSTOM_2, root)
        self.assertIn("Agents › Local", cycled)
        self.assertIn(notice, cycled)
        self.assertIn(
            '\x00theme\x1fconfiguration { timeout { delay: 1; action: "kb-custom-19"; } }',
            cycled,
        )
        self.assertIn("background-refresh:1010;error-notice:1003:", cycled)
        self.assertIn("navigation:", cycled)
        self.assertIn("\x00keep-filter\x1ftrue", cycled)
        self.assertNotIn("\x00keep-selection\x1ftrue", cycled)

        local = NavigationState("local")
        cycled_right = invoke(ROFI_RETV_CUSTOM_2, local)
        self.assertIn("Agents › Alpha", cycled_right)
        self.assertIn(notice, cycled_right)
        self.assertIn("background-refresh:1010;error-notice:1003:", cycled_right)
        self.assertIn("\x00keep-filter\x1ftrue", cycled_right)
        self.assertNotIn("\x00keep-selection\x1ftrue", cycled_right)

        remote = NavigationState("host", "alpha")
        cycled_left = invoke(ROFI_RETV_CUSTOM_3, remote)
        self.assertIn("Agents › Local", cycled_left)
        self.assertIn(notice, cycled_left)
        self.assertIn("background-refresh:1010;error-notice:1003:", cycled_left)
        self.assertIn("\x00keep-filter\x1ftrue", cycled_left)
        self.assertNotIn("\x00keep-selection\x1ftrue", cycled_left)
        store.presentation_context.assert_not_called()

    def test_flat_host_lists_filter_scope_and_sort_newest_first(self) -> None:
        sessions = [
            session(name="older", host="alpha", hostId="alpha", recencyAt=100),
            session(
                "claude",
                "00000000-0000-0000-0000-000000000002",
                name="claude-old",
                host="alpha",
                hostId="alpha",
                recencyAt=150,
            ),
            session(name="newer", host="alpha", hostId="alpha", recencyAt=300),
            session(
                "claude",
                "00000000-0000-0000-0000-000000000003",
                name="claude-new",
                host="elsewhere",
                hostId="elsewhere",
                recencyAt=400,
            ),
        ]
        snapshot = {
            "sessions": sessions,
            "hostCatalog": [
                {"hostId": "workstation", "display": "Workstation", "local": True},
                {"hostId": "alpha", "display": "Alpha", "local": False},
                {"hostId": "elsewhere", "display": "Elsewhere", "local": False},
            ],
            "hosts": {
                "alpha": {"sessions": sessions[:3], "errors": []},
                "elsewhere": {"sessions": [sessions[3]], "errors": []},
            },
            "errors": [],
        }
        output = render_snapshot(
            snapshot,
            navigation=NavigationState("host", "alpha"),
            now=400,
        )
        _, rows = parse_rendered_records(output)
        visible = [parse_row_options(row)[0] for row in rows]
        self.assertEqual(
            ["newer", "claude-old", "older"], [item.split("  ·  ")[0] for item in visible]
        )
        self.assertTrue(all("elsewhere" not in item for item in visible))

        output = render_snapshot(snapshot, navigation=NavigationState("host", "elsewhere"), now=400)
        _, rows = parse_rendered_records(output)
        visible = [parse_row_options(row)[0] for row in rows]
        self.assertEqual(["claude-new"], [item.split("  ·  ")[0] for item in visible])

    def test_open_failure_in_host_view_keeps_background_polling(self) -> None:
        nested = NavigationState("host", "alpha")
        snapshot = {
            "sessions": [session(host="alpha", hostId="alpha")],
            "hostCatalog": [
                {"hostId": "workstation", "display": "Workstation", "local": True},
                {"hostId": "alpha", "display": "Alpha", "local": False},
            ],
            "hosts": {"alpha": {"sessions": [session(host="alpha", hostId="alpha")], "errors": []}},
            "errors": [],
        }
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = snapshot
        data = _refresh_data(1010, 1003, "old notice", navigation=nested)
        with mock.patch(
            "rofi_agent_plus.rofi._open_selection", side_effect=engine.PickerError("gone")
        ):
            output = io.StringIO()
            with (
                mock.patch("sys.stdout", output),
                mock.patch("rofi_agent_plus.rofi.time.time", return_value=1000),
            ):
                run_rofi(
                    {
                        "ROFI_RETV": "1",
                        "ROFI_INFO": selection_payload(session()),
                        "ROFI_DATA": data,
                    },
                    store=store,
                    config=self._config(),
                )
        rendered = output.getvalue()
        self.assertIn("Unable to open session", rendered)
        self.assertIn("Agents › Alpha", rendered)
        self.assertIn("background-refresh:1010;error-notice:", rendered)
        self.assertIn(
            '\x00theme\x1fconfiguration { timeout { delay: 1; action: "kb-custom-19"; } }',
            rendered,
        )

    def test_poll_callback_reuses_prepared_presentation_context(self) -> None:
        store = mock.Mock(spec=CacheStore)
        context = PresentationContext(
            self._config().fingerprint,
            {
                "kind": "contract",
                "capability": "host-mesh-v1+tmux-session-v1",
                "meshRevision": "sha256:mesh",
            },
        )
        snapshot = {"sessions": [], "hostCatalog": [], "hosts": {}, "errors": []}
        store.presentation_context.return_value = context
        store.load_current.return_value = snapshot
        store.is_fresh.return_value = True
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            run_rofi(
                {
                    "ROFI_RETV": str(ROFI_RETV_CUSTOM_19),
                    "ROFI_DATA": _navigation_data(NavigationState()),
                },
                store=store,
                config=self._config(),
            )
        store.presentation_context.assert_called_once_with(self._config())
        store.load_current.assert_called_once_with(self._config(), context)

    def test_alt_r_starts_async_refresh_and_retains_current_rows(self) -> None:
        nested = NavigationState("host", "alpha")
        selected = session("claude", THREAD_ID, host="alpha", hostId="alpha")
        snapshot = {
            "sessions": [selected],
            "hostCatalog": [
                {"hostId": "workstation", "display": "Workstation", "local": True},
                {"hostId": "alpha", "display": "Alpha", "local": False},
            ],
            "hosts": {"alpha": {"sessions": [selected], "errors": []}},
            "errors": [],
        }
        store = mock.Mock(spec=CacheStore)
        context = PresentationContext(
            self._config().fingerprint,
            {
                "kind": "contract",
                "capability": "host-mesh-v1+tmux-session-v1",
                "meshRevision": "sha256:mesh",
            },
        )
        scope = {"fingerprint": self._config().fingerprint, "backend": context.backend}
        store.presentation_context.return_value = context
        store.load_current.return_value = snapshot
        store.cache_scope.return_value = scope
        store.spawn_background.return_value = True
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            run_rofi(
                {
                    "ROFI_RETV": str(ROFI_RETV_CUSTOM_1),
                    "ROFI_DATA": _navigation_data(nested),
                },
                store=store,
                config=self._config(),
            )
        rendered = output.getvalue()
        store.spawn_background.assert_called_once()
        self.assertEqual(scope, store.spawn_background.call_args.kwargs["scope"])
        store.refresh.assert_not_called()
        self.assertIn("Agents › Alpha", rendered)
        self.assertIn("navigation:", rendered)
        self.assertIn("claude", rendered)
        self.assertIn("Checking sessions…", rendered)
        self.assertIn("background-refresh:", rendered)
        self.assertIn("\x00keep-filter\x1ftrue", rendered)
        self.assertIn("\x00keep-selection\x1ftrue", rendered)

    def test_alt_r_reports_worker_start_failure_without_refreshing(self) -> None:
        snapshot = {"sessions": [session()], "errors": []}
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = snapshot
        store.spawn_background.return_value = False
        store.background_active.return_value = False
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            run_rofi(
                {
                    "ROFI_RETV": str(ROFI_RETV_CUSTOM_1),
                    "ROFI_DATA": _navigation_data(NavigationState()),
                },
                store=store,
                config=self._config(),
            )
        rendered = output.getvalue()
        store.spawn_background.assert_called_once()
        store.refresh.assert_not_called()
        self.assertIn("Unable to start background refresh", rendered)
        self.assertIn("hello", rendered)

    def test_left_right_cycle_host_scope_ring_and_wrap_in_both_directions(self) -> None:
        store = mock.Mock(spec=CacheStore)
        snapshot = {
            "sessions": [session()],
            "hostCatalog": [
                {"hostId": "workstation", "display": "Workstation", "local": True},
                {"hostId": "alpha", "display": "Alpha", "local": False},
                {"hostId": "beta", "display": "Beta", "local": False},
            ],
            "hosts": {"workstation": {"sessions": [session()], "errors": []}},
            "errors": [],
        }
        store.load.return_value = snapshot
        for retv, state, expected in (
            (ROFI_RETV_CUSTOM_2, NavigationState(), "Local"),
            (ROFI_RETV_CUSTOM_2, NavigationState("local"), "Alpha"),
            (ROFI_RETV_CUSTOM_2, NavigationState("host", "alpha"), "Beta"),
            (ROFI_RETV_CUSTOM_2, NavigationState("host", "beta"), "All"),
            (ROFI_RETV_CUSTOM_3, NavigationState(), "Beta"),
            (ROFI_RETV_CUSTOM_3, NavigationState("host", "beta"), "Alpha"),
            (ROFI_RETV_CUSTOM_3, NavigationState("host", "alpha"), "Local"),
            (ROFI_RETV_CUSTOM_3, NavigationState("local"), "All"),
        ):
            output = io.StringIO()
            with (
                mock.patch("sys.stdout", output),
                mock.patch("rofi_agent_plus.rofi.time.time", return_value=1000),
            ):
                run_rofi(
                    {
                        "ROFI_RETV": str(retv),
                        "ROFI_DATA": _refresh_data(1010, 1003, "offline", navigation=state),
                    },
                    store=store,
                    config=self._config(),
                )
            rendered = output.getvalue()
            self.assertIn(f"\x00prompt\x1fAgents › {expected}", rendered)
            self.assertIn("background-refresh:1010;error-notice:1003:", rendered)
            self.assertIn("\x00keep-filter\x1ftrue", rendered)
            self.assertNotIn("\x00keep-selection\x1ftrue", rendered)

    def test_escape_migration_guard_always_closes_without_work(self) -> None:
        for state in (
            NavigationState(),
            NavigationState("local"),
            NavigationState("host", "alpha"),
        ):
            with self.subTest(state=state):
                store = mock.Mock(spec=CacheStore)
                output = io.StringIO()
                with mock.patch("sys.stdout", output):
                    result = run_rofi(
                        {
                            "ROFI_RETV": str(ROFI_RETV_CUSTOM_6),
                            "ROFI_DATA": _navigation_data(state),
                        },
                        store=store,
                        config=self._config(),
                    )
                self.assertEqual(0, result)
                self.assertEqual("", output.getvalue())
                store.load.assert_not_called()
                store.presentation_context.assert_not_called()
                store.refresh.assert_not_called()

        output = io.StringIO()
        with (
            mock.patch("sys.stdout", output),
            mock.patch(
                "rofi_agent_plus.rofi.load_config", side_effect=ConfigError("invalid config")
            ),
        ):
            result = run_rofi({"ROFI_RETV": str(ROFI_RETV_CUSTOM_6)}, store=mock.Mock())
        self.assertEqual(0, result)
        self.assertEqual("", output.getvalue())

    def test_enter_opens_leaf_and_rejects_forged_group_metadata(self) -> None:
        snapshot = {"sessions": [session()], "errors": []}
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = snapshot
        with mock.patch("rofi_agent_plus.rofi._open_selection") as opener:
            self.assertEqual(
                0,
                run_rofi(
                    {
                        "ROFI_RETV": "1",
                        "ROFI_INFO": selection_payload(session()),
                    },
                    store=store,
                    config=self._config(),
                ),
            )
        opener.assert_called_once()

        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            run_rofi(
                {
                    "ROFI_RETV": "1",
                    "ROFI_INFO": json.dumps(
                        {"type": "group", "groupType": "host", "value": "workstation"}
                    ),
                },
                store=store,
                config=self._config(),
            )
        self.assertIn("Unable to open session", output.getvalue())
        opener.assert_called_once()

        forged = json.loads(selection_payload(session()))
        forged.update({"type": "group", "groupType": "host", "value": "workstation"})
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            run_rofi(
                {
                    "ROFI_RETV": "1",
                    "ROFI_INFO": json.dumps(forged),
                },
                store=store,
                config=self._config(),
            )
        self.assertIn("Unable to open session", output.getvalue())
        opener.assert_called_once()

    def test_refresh_and_open_failure_preserve_empty_host_scope(self) -> None:
        nested = NavigationState("host", "gone-host")
        stale = {
            "sessions": [session(host="other-host", hostId="other-host")],
            "hostCatalog": [
                {"hostId": "workstation", "display": "Workstation", "local": True},
                {"hostId": "gone-host", "display": "Gone", "local": False},
            ],
            "hosts": {},
            "errors": [],
        }
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = stale
        store.is_fresh.return_value = True
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            run_rofi(
                {
                    "ROFI_RETV": str(ROFI_RETV_CUSTOM_19),
                    "ROFI_DATA": _navigation_data(nested),
                },
                store=store,
                config=self._config(),
            )
        rendered = output.getvalue()
        self.assertIn("Agents › Gone", rendered)
        self.assertIn("No agent sessions on Gone", rendered)
        self.assertIn("\x00keep-filter\x1ftrue", rendered)
        self.assertIn("\x00data\x1fnavigation:", rendered)

        with mock.patch(
            "rofi_agent_plus.rofi._open_selection", side_effect=engine.PickerError("gone")
        ):
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                run_rofi(
                    {
                        "ROFI_RETV": "1",
                        "ROFI_INFO": selection_payload(session()),
                        "ROFI_DATA": _navigation_data(nested),
                    },
                    store=store,
                    config=self._config(),
                )
        rendered = output.getvalue()
        self.assertIn("Unable to open session", rendered)
        self.assertIn("Agents › Gone", rendered)
        self.assertIn("\x00keep-selection\x1ftrue", rendered)

    def test_selection_parser_validates_provider_ids(self) -> None:
        payload = json.loads(selection_payload(session("opencode", OPENCODE_ID)))
        self.assertEqual(payload, _parse_selection(json.dumps(payload)))
        with self.assertRaises(engine.PickerError):
            _parse_selection(json.dumps({"kind": "codex", "id": "bad"}))

    def test_selection_parser_requires_a_boolean_option_evidence_marker(self) -> None:
        payload = json.loads(selection_payload(session(providerOptionVerified=True)))
        self.assertIs(payload["providerOptionVerified"], True)
        self.assertIs(
            _parse_selection(json.dumps(payload))["providerOptionVerified"],
            True,
        )
        for invalid in (None, "true", 0, 1, [], {}):
            malformed = dict(payload)
            malformed["providerOptionVerified"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(engine.PickerError):
                _parse_selection(json.dumps(malformed))

    def test_stale_tmux_rows_cannot_export_option_evidence(self) -> None:
        payload = json.loads(
            selection_payload(
                session(
                    providerOptionVerified=True,
                    tmuxStale=True,
                    tmux={
                        "meshRevision": None,
                        "serverGeneration": "tmux-v1:local",
                        "sessionId": "$4",
                        "createdAt": 5,
                        "observedName": "agent",
                    },
                )
            )
        )
        self.assertIs(payload["providerOptionVerified"], False)

    def test_option_backed_selection_fast_path_skips_model_and_cache_work(self) -> None:
        selected = session(
            backend={
                "kind": "contract",
                "capability": "host-mesh-v1+tmux-session-v1",
                "meshRevision": "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
            },
            providerOptionVerified=True,
            tmux={
                "meshRevision": "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
                "serverGeneration": "tmux-v1:remote",
                "sessionId": "$4",
                "createdAt": 5,
                "observedName": "agent",
            },
        )
        store = mock.Mock(spec=CacheStore)
        with (
            mock.patch("rofi_agent_plus.rofi.fast_open_selection") as fast_open,
            mock.patch("sys.stdout", new=io.StringIO()),
        ):
            self.assertEqual(
                0,
                run_rofi(
                    {"ROFI_RETV": "1", "ROFI_INFO": selection_payload(selected)},
                    store=store,
                    config=self._config(),
                ),
            )
        fast_open.assert_called_once()
        store.presentation_context.assert_not_called()
        store.load_current.assert_not_called()
        store.refresh.assert_not_called()

    def test_null_authority_selection_skips_fast_path_and_uses_full_open(self) -> None:
        selected = session(
            providerOptionVerified=True,
            tmux={
                "meshRevision": None,
                "serverGeneration": "tmux-v1:local",
                "sessionId": "$4",
                "createdAt": 5,
                "observedName": "agent",
            },
        )
        config = self._config()
        context = PresentationContext(config.fingerprint, selected["backend"], selected=object())
        store = mock.Mock(spec=CacheStore)
        store.presentation_context.return_value = context
        with (
            mock.patch("rofi_agent_plus.rofi.fast_open_selection") as fast_open,
            mock.patch("rofi_agent_plus.rofi._open_selection") as full_open,
            mock.patch("sys.stdout", new=io.StringIO()),
        ):
            self.assertEqual(
                0,
                run_rofi(
                    {"ROFI_RETV": "1", "ROFI_INFO": selection_payload(selected)},
                    store=store,
                    config=config,
                ),
            )
        fast_open.assert_not_called()
        full_open.assert_called_once()

    def test_fast_path_safe_contract_errors_fall_back_once_to_full_open(self) -> None:
        selected = session(
            backend={
                "kind": "contract",
                "capability": "host-mesh-v1+tmux-session-v1",
                "meshRevision": "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
            },
            providerOptionVerified=True,
            tmux={
                "meshRevision": "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
                "serverGeneration": "tmux-v1:remote",
                "sessionId": "$4",
                "createdAt": 5,
                "observedName": "agent",
            },
        )
        context = PresentationContext(
            self._config().fingerprint,
            selected["backend"],
            selected=object(),
        )
        for code in ("stale_session", "session_not_found", "stale_mesh", "invalid_input"):
            with self.subTest(code=code):
                store = mock.Mock(spec=CacheStore)
                store.presentation_context.return_value = context
                with (
                    mock.patch(
                        "rofi_agent_plus.rofi.fast_open_selection",
                        side_effect=LifecycleError(code, "no action"),
                    ),
                    mock.patch("rofi_agent_plus.rofi._open_selection") as full_open,
                    mock.patch("sys.stdout", new=io.StringIO()),
                ):
                    run_rofi(
                        {"ROFI_RETV": "1", "ROFI_INFO": selection_payload(selected)},
                        store=store,
                        config=self._config(),
                    )
                full_open.assert_called_once()

    def test_fast_path_ambiguous_error_never_retries_or_opens_full_path(self) -> None:
        selected = session(
            backend={
                "kind": "contract",
                "capability": "host-mesh-v1+tmux-session-v1",
                "meshRevision": "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
            },
            providerOptionVerified=True,
            tmux={
                "meshRevision": "sha256:c932eaa7fc77de0590085a5916d5ea823eccce0ba22157f091549ed9ad5c1262",
                "serverGeneration": "tmux-v1:remote",
                "sessionId": "$4",
                "createdAt": 5,
                "observedName": "agent",
            },
        )
        context = PresentationContext(
            self._config().fingerprint,
            selected["backend"],
            selected=object(),
        )
        store = mock.Mock(spec=CacheStore)
        store.presentation_context.return_value = context
        store.load_current.return_value = {"sessions": [selected], "errors": []}
        output = io.StringIO()
        with (
            mock.patch(
                "rofi_agent_plus.rofi.fast_open_selection",
                side_effect=LifecycleError("operation_failed", "timed out"),
            ) as fast_open,
            mock.patch("rofi_agent_plus.rofi._open_selection") as full_open,
            mock.patch("sys.stdout", output),
        ):
            run_rofi(
                {"ROFI_RETV": "1", "ROFI_INFO": selection_payload(selected)},
                store=store,
                config=self._config(),
            )
        fast_open.assert_called_once()
        full_open.assert_not_called()
        self.assertIn("Unable to open session", output.getvalue())

    def test_initial_mode_refreshes_cache_and_alt_r_starts_background_refresh(self) -> None:
        store = mock.Mock(spec=CacheStore)
        snapshot = {"sessions": [session()], "errors": []}
        store.load.side_effect = [None, snapshot]
        store.refresh.return_value = snapshot
        store.is_fresh.return_value = True
        store.spawn_background.return_value = True
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            result = run_rofi({"ROFI_RETV": "0"}, store=store, config=self._config())
        self.assertEqual(0, result)
        store.refresh.assert_called_once()
        initial_output = output.getvalue()
        self.assertIn("Agents", initial_output)
        initial_headers, initial_rows = parse_rendered_records(initial_output)
        self.assertIn(f"\x00delim\x1f{ROFI_DELIMITER_VALUE}", initial_headers)
        self.assertEqual(1, len(initial_rows))
        self.assertIn(ROW_SEPARATOR, parse_row_options(initial_rows[0])[1]["display"])

        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            result = run_rofi({"ROFI_RETV": "10"}, store=store, config=self._config())
        self.assertEqual(0, result)
        self.assertEqual(1, store.refresh.call_count)
        store.spawn_background.assert_called_once()
        callback_output = output.getvalue()
        self.assertIn("\x00keep-selection\x1ftrue", callback_output)
        self.assertIn("\x00keep-filter\x1ftrue", callback_output)
        self.assertIn("Checking sessions…", callback_output)
        callback_headers, callback_rows = parse_rendered_records(callback_output)
        self.assertNotIn(f"\x00delim\x1f{ROFI_DELIMITER_VALUE}", callback_headers)
        self.assertTrue(callback_output.endswith(ROFI_RECORD_SEPARATOR))
        self.assertEqual(1, len(callback_rows))
        callback_display = parse_row_options(callback_rows[0])[1]["display"]
        self.assertIn(ROW_SEPARATOR, callback_display)
        self.assertIn("◌ Checking", callback_display)

        failing_store = mock.Mock(spec=CacheStore)
        failing_store.load.return_value = {"sessions": [session()], "errors": []}
        failing_store.spawn_background.return_value = False
        failing_store.background_active.return_value = False
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            run_rofi({"ROFI_RETV": "10"}, store=failing_store, config=self._config())
        self.assertIn("Unable to start background refresh", output.getvalue())
        failing_store.refresh.assert_not_called()
        self.assertIn("\x00keep-selection\x1ftrue", output.getvalue())
        self.assertIn("\x00keep-filter\x1ftrue", output.getvalue())

    def test_stale_mode_renders_immediately_and_starts_one_background_refresh(self) -> None:
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = {"sessions": [session()], "errors": []}
        store.is_fresh.return_value = False
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            run_rofi({"ROFI_RETV": "0"}, store=store, config=self._config())
        store.spawn_background.assert_called_once()
        rendered = output.getvalue()
        self.assertIn("Checking sessions…", rendered)
        self.assertIn(
            '\x00theme\x1fconfiguration { timeout { delay: 1; action: "kb-custom-19"; } }', rendered
        )
        self.assertIn(f"\x00data\x1f{AUTO_REFRESH_DATA_PREFIX}", rendered)
        _, rows = parse_rendered_records(rendered)
        self.assertIn("◌ Checking", parse_row_options(rows[0])[1]["display"])

    def test_stale_mode_only_shows_refresh_status_for_previous_errors(self) -> None:
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = {
            "sessions": [session()],
            "errors": [{"host": "local", "stage": "threads", "message": "offline"}],
        }
        store.is_fresh.return_value = False
        store.spawn_background.return_value = True
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            run_rofi({"ROFI_RETV": "0"}, store=store, config=self._config())
        rendered = output.getvalue()
        self.assertIn("Checking sessions…", rendered)
        self.assertNotIn("Refresh errors: local/threads: offline", rendered)

    def test_stale_mode_clears_spawn_failure_status_without_enabling_polling(self) -> None:
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = {"sessions": [session()], "errors": []}
        store.is_fresh.return_value = False
        store.spawn_background.return_value = False
        store.background_active.return_value = False
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            run_rofi({"ROFI_RETV": "0"}, store=store, config=self._config())
        store.spawn_background.assert_called_once()
        rendered = output.getvalue()
        self.assertNotIn("Checking sessions…", rendered)
        self.assertNotIn("Background refresh stopped", rendered)
        self.assertNotIn("\x00message\x1f", rendered)
        self.assertNotIn("\x00theme\x1f", rendered)

    def test_background_callback_polls_without_starting_another_refresh(self) -> None:
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = {"sessions": [session()], "errors": []}
        store.is_fresh.return_value = False
        store.background_active.return_value = True
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            result = run_rofi(
                {
                    "ROFI_RETV": str(ROFI_RETV_CUSTOM_19),
                    "ROFI_DATA": f"{AUTO_REFRESH_DATA_PREFIX}{int(time.time()) + 10}",
                },
                store=store,
                config=self._config(),
            )
        self.assertEqual(0, result)
        store.refresh.assert_not_called()
        store.spawn_background.assert_not_called()
        rendered = output.getvalue()
        self.assertIn("Checking sessions…", rendered)
        self.assertIn("\x00keep-selection\x1ftrue", rendered)
        self.assertIn("\x00keep-filter\x1ftrue", rendered)
        self.assertIn(
            '\x00theme\x1fconfiguration { timeout { delay: 1; action: "kb-custom-19"; } }', rendered
        )
        _, rows = parse_rendered_records(rendered)
        self.assertIn("◌ Checking", parse_row_options(rows[0])[1]["display"])

    def test_background_and_error_continuations_coexist_until_notice_expiry(self) -> None:
        snapshot = {"sessions": [session()], "errors": []}
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = snapshot
        store.is_fresh.return_value = False
        store.background_active.return_value = True
        error_message = "Refresh errors: local/threads: offline"
        data = _refresh_data(1010, 1003, error_message)
        environ = {"ROFI_RETV": str(ROFI_RETV_CUSTOM_19), "ROFI_DATA": data}

        output = io.StringIO()
        with (
            mock.patch("sys.stdout", output),
            mock.patch("rofi_agent_plus.rofi.time.time", return_value=1000),
        ):
            result = run_rofi(environ, store=store, config=self._config())
        self.assertEqual(0, result)
        first = output.getvalue()
        self.assertIn(error_message, first)
        self.assertIn("hello", first)
        _, rows = parse_rendered_records(first)
        self.assertIn("◌ Checking", parse_row_options(rows[0])[1]["display"])
        self.assertIn("\x00keep-selection\x1ftrue", first)
        self.assertIn("\x00keep-filter\x1ftrue", first)
        self.assertIn(
            '\x00theme\x1fconfiguration { timeout { delay: 1; action: "kb-custom-19"; } }',
            first,
        )
        continuation_data = first.split("\x00data\x1f", 1)[1].split("\t", 1)[0]
        self.assertTrue(continuation_data.startswith("background-refresh:1010;"))
        self.assertIn("error-notice:1003:", continuation_data)
        self.assertEqual((1003.0, error_message), _parse_error_notice(continuation_data))

        output = io.StringIO()
        with (
            mock.patch("sys.stdout", output),
            mock.patch("rofi_agent_plus.rofi.time.time", return_value=1003),
        ):
            result = run_rofi(
                {"ROFI_RETV": str(ROFI_RETV_CUSTOM_19), "ROFI_DATA": continuation_data},
                store=store,
                config=self._config(),
            )
        self.assertEqual(0, result)
        expired = output.getvalue()
        self.assertNotIn(error_message, expired)
        self.assertIn("Checking sessions…", expired)
        self.assertIn("\x00keep-selection\x1ftrue", expired)
        self.assertIn("\x00keep-filter\x1ftrue", expired)
        self.assertIn(
            '\x00theme\x1fconfiguration { timeout { delay: 1; action: "kb-custom-19"; } }',
            expired,
        )
        self.assertIn("\x00data\x1fbackground-refresh:1010", expired)
        self.assertNotIn("error-notice:", expired)
        store.refresh.assert_not_called()
        store.spawn_background.assert_not_called()

    def test_background_callback_renders_fresh_rows_and_disables_polling(self) -> None:
        fresh = session(name="fresh", recencyAt=int(time.time()))
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = {"sessions": [fresh], "errors": []}
        store.is_fresh.return_value = True
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            result = run_rofi(
                {"ROFI_RETV": str(ROFI_RETV_CUSTOM_19)},
                store=store,
                config=self._config(),
            )
        self.assertEqual(0, result)
        store.refresh.assert_not_called()
        store.spawn_background.assert_not_called()
        store.background_active.assert_not_called()
        rendered = output.getvalue()
        self.assertIn("fresh", rendered)
        self.assertNotIn("Checking sessions…", rendered)
        self.assertIn("\x00message\x1f", rendered)
        self.assertIn(
            '\x00theme\x1fconfiguration { timeout { delay: 0; action: "kb-custom-19"; } }', rendered
        )
        self.assertIn("\x00data\x1fidle", rendered)

    def test_background_completion_starts_a_bounded_check_notice(self) -> None:
        fresh = session(name="fresh", recencyAt=1000)
        snapshot = {"sessions": [fresh], "errors": []}
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = snapshot
        store.is_fresh.return_value = True
        output = io.StringIO()
        with (
            mock.patch("sys.stdout", output),
            mock.patch("rofi_agent_plus.rofi.time.time", return_value=1000),
        ):
            result = run_rofi(
                {
                    "ROFI_RETV": str(ROFI_RETV_CUSTOM_19),
                    "ROFI_DATA": _refresh_data(1010),
                },
                store=store,
                config=self._config(),
            )
        self.assertEqual(0, result)
        rendered = output.getvalue()
        self.assertIn("Checked just now", rendered)
        self.assertIn(f"\x00data\x1f{CHECK_NOTICE_DATA_PREFIX}1002", rendered)
        self.assertNotIn(AUTO_REFRESH_DATA_PREFIX, rendered)
        self.assertNotIn("◌ Checking", rendered)
        _, rows = parse_rendered_records(rendered)
        _, options = parse_row_options(rows[0])
        self.assertNotIn("Checking", options["display"])

    def test_completion_notice_persists_through_navigation_then_clears_on_expiry(self) -> None:
        selected = session(name="fresh", recencyAt=1000)
        snapshot = {
            "sessions": [selected],
            "hostCatalog": [
                {"hostId": "workstation", "display": "Workstation", "local": True},
                {"hostId": "alpha", "display": "Alpha", "local": False},
            ],
            "hosts": {"workstation": {"sessions": [selected], "errors": []}},
            "errors": [],
        }
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = snapshot
        store.is_fresh.return_value = True
        completion = _refresh_data(1001, check_deadline=1002)

        output = io.StringIO()
        with (
            mock.patch("sys.stdout", output),
            mock.patch("rofi_agent_plus.rofi.time.time", return_value=1001),
        ):
            run_rofi(
                {"ROFI_RETV": str(ROFI_RETV_CUSTOM_2), "ROFI_DATA": completion},
                store=store,
                config=self._config(),
            )
        navigated = output.getvalue()
        self.assertIn("Checked just now", navigated)
        self.assertIn("Agents › Local", navigated)
        self.assertIn(f"{CHECK_NOTICE_DATA_PREFIX}1002", navigated)
        self.assertIn("\x00keep-filter\x1ftrue", navigated)
        self.assertNotIn("\x00keep-selection\x1ftrue", navigated)

        output = io.StringIO()
        with (
            mock.patch("sys.stdout", output),
            mock.patch("rofi_agent_plus.rofi.time.time", return_value=1002),
        ):
            run_rofi(
                {"ROFI_RETV": str(ROFI_RETV_CUSTOM_19), "ROFI_DATA": completion},
                store=store,
                config=self._config(),
            )
        expired = output.getvalue()
        self.assertNotIn("Checked just now", expired)
        self.assertIn("\x00message\x1f\t", expired)
        self.assertIn("\x00data\x1fidle", expired)
        self.assertIn(
            '\x00theme\x1fconfiguration { timeout { delay: 0; action: "kb-custom-19"; } }',
            expired,
        )

    def test_fresh_initial_callback_without_refresh_witness_has_no_check_notice(self) -> None:
        snapshot = {"sessions": [session(name="fresh")], "errors": []}
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = snapshot
        store.is_fresh.return_value = True
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            run_rofi(
                {"ROFI_RETV": str(ROFI_RETV_CUSTOM_19)},
                store=store,
                config=self._config(),
            )
        rendered = output.getvalue()
        self.assertNotIn("Checked just now", rendered)
        self.assertNotIn(CHECK_NOTICE_DATA_PREFIX, rendered)
        self.assertIn("\x00data\x1fidle", rendered)

    def test_completed_refresh_errors_take_precedence_over_check_notice(self) -> None:
        snapshot = {
            "sessions": [session()],
            "errors": [{"host": "local", "stage": "active", "message": "offline"}],
        }
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = snapshot
        store.is_fresh.return_value = True
        output = io.StringIO()
        with (
            mock.patch("sys.stdout", output),
            mock.patch("rofi_agent_plus.rofi.time.time", return_value=1000),
        ):
            run_rofi(
                {
                    "ROFI_RETV": str(ROFI_RETV_CUSTOM_19),
                    "ROFI_DATA": _refresh_data(1010),
                },
                store=store,
                config=self._config(),
            )
        rendered = output.getvalue()
        self.assertIn("Refresh errors: local/active: offline", rendered)
        self.assertNotIn("Checked just now", rendered)
        self.assertNotIn(CHECK_NOTICE_DATA_PREFIX, rendered)
        self.assertNotIn(AUTO_REFRESH_DATA_PREFIX, rendered)

    def test_marker_stop_reports_failed_or_stopped_check_and_then_clears(self) -> None:
        for outcome, expected in (
            ("failed", "Check failed · showing last-known results"),
            ("complete", "Check stopped · showing last-known results"),
        ):
            with self.subTest(outcome=outcome):
                snapshot = {
                    "sessions": [session()],
                    "lastRefresh": {
                        "attemptedAt": 900,
                        "completedAt": None if outcome == "failed" else 900,
                        "outcome": outcome,
                    },
                    "errors": [],
                }
                store = mock.Mock(spec=CacheStore)
                store.load.return_value = snapshot
                store.is_fresh.return_value = False
                store.background_active.return_value = False
                data = _refresh_data(1010)
                output = io.StringIO()
                with (
                    mock.patch("sys.stdout", output),
                    mock.patch("rofi_agent_plus.rofi.time.time", return_value=1000),
                ):
                    run_rofi(
                        {"ROFI_RETV": str(ROFI_RETV_CUSTOM_19), "ROFI_DATA": data},
                        store=store,
                        config=self._config(),
                    )
                rendered = output.getvalue()
                self.assertIn(expected, rendered)
                self.assertIn(f"{ERROR_NOTICE_DATA_PREFIX}", rendered)
                self.assertIn("◷ Last known", rendered) if outcome == "failed" else None
                self.assertNotIn(AUTO_REFRESH_DATA_PREFIX, rendered)

                continuation = rendered.split("\x00data\x1f", 1)[1].split("\t", 1)[0]
                output = io.StringIO()
                with (
                    mock.patch("sys.stdout", output),
                    mock.patch("rofi_agent_plus.rofi.time.time", return_value=1003),
                ):
                    run_rofi(
                        {"ROFI_RETV": str(ROFI_RETV_CUSTOM_19), "ROFI_DATA": continuation},
                        store=store,
                        config=self._config(),
                    )
                expired = output.getvalue()
                self.assertNotIn(expected, expired)
                self.assertIn("\x00data\x1fidle", expired)

    def test_fresh_error_snapshot_uses_a_bounded_notice_timeout(self) -> None:
        snapshot = {
            "sessions": [session()],
            "errors": [{"host": "local", "stage": "threads", "message": "offline"}],
        }
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = snapshot
        store.is_fresh.return_value = True
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            result = run_rofi({"ROFI_RETV": "0"}, store=store, config=self._config())
        self.assertEqual(0, result)
        rendered = output.getvalue()
        self.assertIn("Refresh errors: local/threads: offline", rendered)
        self.assertIn(
            f'\x00theme\x1fconfiguration {{ timeout {{ delay: {ERROR_NOTICE_SECONDS}; action: "kb-custom-19"; }} }}',
            rendered,
        )
        self.assertIn(f"\x00data\x1f{ERROR_NOTICE_DATA_PREFIX}", rendered)
        deadline, message = _parse_error_notice(
            rendered.split("\x00data\x1f", 1)[1].split("\n", 1)[0]
        )
        self.assertIsNotNone(deadline)
        self.assertEqual("Refresh errors: local/threads: offline", message)

    def test_error_notice_callback_clears_without_snapshot_error_fallback(self) -> None:
        snapshot = {
            "sessions": [session()],
            "errors": [{"host": "local", "stage": "threads", "message": "offline"}],
        }
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = snapshot
        store.is_fresh.return_value = True
        expired = (
            f"{ERROR_NOTICE_DATA_PREFIX}{int(time.time()) - 1}:"
            "Refresh%20errors%3A%20local%2Fthreads%3A%20offline"
        )
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            result = run_rofi(
                {"ROFI_RETV": str(ROFI_RETV_CUSTOM_19), "ROFI_DATA": expired},
                store=store,
                config=self._config(),
            )
        self.assertEqual(0, result)
        rendered = output.getvalue()
        self.assertNotIn("Refresh errors: local/threads: offline", rendered)
        self.assertIn("\x00message\x1f\t", rendered)
        self.assertIn(
            '\x00theme\x1fconfiguration { timeout { delay: 0; action: "kb-custom-19"; } }',
            rendered,
        )
        self.assertIn("\x00data\x1fidle", rendered)

    def test_background_completion_with_errors_starts_bounded_notice(self) -> None:
        snapshot = {
            "sessions": [session()],
            "errors": [{"host": "local", "stage": "active", "message": "offline"}],
        }
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = snapshot
        store.is_fresh.return_value = True
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            result = run_rofi(
                {
                    "ROFI_RETV": str(ROFI_RETV_CUSTOM_19),
                    "ROFI_DATA": f"{AUTO_REFRESH_DATA_PREFIX}{int(time.time()) + 10}",
                },
                store=store,
                config=self._config(),
            )
        self.assertEqual(0, result)
        rendered = output.getvalue()
        self.assertIn("Refresh errors: local/active: offline", rendered)
        self.assertIn(f"\x00data\x1f{ERROR_NOTICE_DATA_PREFIX}", rendered)
        self.assertNotIn(f"\x00data\x1f{AUTO_REFRESH_DATA_PREFIX}", rendered)

    def test_alt_r_worker_start_failure_uses_the_same_bounded_notice_contract(self) -> None:
        snapshot = {
            "sessions": [session()],
            "errors": [{"host": "local", "stage": "threads", "message": "offline"}],
        }
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = snapshot
        store.spawn_background.return_value = False
        store.background_active.return_value = False
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            result = run_rofi(
                {"ROFI_RETV": "10"},
                store=store,
                config=self._config(),
            )
        self.assertEqual(0, result)
        rendered = output.getvalue()
        self.assertIn("Unable to start background refresh", rendered)
        self.assertIn(f"\x00data\x1f{ERROR_NOTICE_DATA_PREFIX}", rendered)
        self.assertIn("\x00keep-selection\x1ftrue", rendered)
        store.refresh.assert_not_called()

    def test_background_callback_rereads_after_marker_stop_and_prefers_fresh_cache(self) -> None:
        stale = {"sessions": [session(name="stale")], "errors": []}
        fresh = {"sessions": [session(name="fresh")], "errors": []}
        store = mock.Mock(spec=CacheStore)
        store.load.side_effect = [stale, fresh]
        store.is_fresh.side_effect = [False, True]
        store.background_active.return_value = False
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            result = run_rofi(
                {"ROFI_RETV": str(ROFI_RETV_CUSTOM_19)},
                store=store,
                config=self._config(),
            )
        self.assertEqual(0, result)
        store.refresh.assert_not_called()
        store.spawn_background.assert_not_called()
        self.assertEqual(2, store.load.call_count)
        rendered = output.getvalue()
        self.assertIn("fresh", rendered)
        self.assertNotIn("Background refresh stopped", rendered)
        self.assertNotIn("Checking sessions…", rendered)
        self.assertIn("\x00message\x1f", rendered)
        self.assertIn(
            '\x00theme\x1fconfiguration { timeout { delay: 0; action: "kb-custom-19"; } }', rendered
        )

    def test_background_callback_stops_on_worker_failure_or_stall(self) -> None:
        stale = {"sessions": [session()], "errors": []}
        for data, marker_active in (
            (None, False),
            (f"{AUTO_REFRESH_DATA_PREFIX}{int(time.time()) - 1}", True),
        ):
            with self.subTest(data=data, marker_active=marker_active):
                store = mock.Mock(spec=CacheStore)
                store.load.return_value = stale
                store.is_fresh.return_value = False
                store.background_active.return_value = marker_active
                environ = {"ROFI_RETV": str(ROFI_RETV_CUSTOM_19)}
                if data is not None:
                    environ["ROFI_DATA"] = data
                output = io.StringIO()
                with mock.patch("sys.stdout", output):
                    result = run_rofi(environ, store=store, config=self._config())
                self.assertEqual(0, result)
                store.refresh.assert_not_called()
                store.spawn_background.assert_not_called()
                rendered = output.getvalue()
                if data is None:
                    self.assertNotIn("Check stopped", rendered)
                    self.assertNotIn("Checking sessions…", rendered)
                    self.assertIn("\x00message\x1f\t", rendered)
                    self.assertIn(
                        '\x00theme\x1fconfiguration { timeout { delay: 0; action: "kb-custom-19"; } }',
                        rendered,
                    )
                else:
                    self.assertIn("Check stopped · showing last-known results", rendered)
                    self.assertNotIn("Checking sessions…", rendered)
                    self.assertIn(f"\x00data\x1f{ERROR_NOTICE_DATA_PREFIX}", rendered)
                    self.assertIn(
                        f'\x00theme\x1fconfiguration {{ timeout {{ delay: {ERROR_NOTICE_SECONDS}; action: "kb-custom-19"; }} }}',
                        rendered,
                    )

    def test_selection_success_closes_and_failure_rerenders(self) -> None:
        selected = session()
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = {"sessions": [selected], "errors": []}
        with mock.patch("rofi_agent_plus.rofi._open_selection") as opener:
            self.assertEqual(
                0,
                run_rofi(
                    {"ROFI_RETV": "1", "ROFI_INFO": json.dumps(selected)},
                    store=store,
                    config=self._config(),
                ),
            )
            opener.assert_called_once()
        opener.side_effect = engine.PickerError("gone")
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            run_rofi(
                {"ROFI_RETV": "1", "ROFI_INFO": json.dumps(selected)},
                store=store,
                config=self._config(),
            )
        self.assertIn("Unable to open session", output.getvalue())
        self.assertIn("\x00keep-selection\x1ftrue", output.getvalue())
        self.assertIn("\x00keep-filter\x1ftrue", output.getvalue())
        self.assertIn(f"\x00data\x1f{ERROR_NOTICE_DATA_PREFIX}", output.getvalue())
        self.assertIn(
            f'\x00theme\x1fconfiguration {{ timeout {{ delay: {ERROR_NOTICE_SECONDS}; action: "kb-custom-19"; }} }}',
            output.getvalue(),
        )

        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            run_rofi(
                {"ROFI_RETV": "1", "ROFI_INFO": "not-json"},
                store=store,
                config=self._config(),
            )
        self.assertIn("\x00keep-selection\x1ftrue", output.getvalue())
        self.assertIn("\x00keep-filter\x1ftrue", output.getvalue())

    def test_custom_and_delete_callbacks_do_not_mutate_rows(self) -> None:
        selected = session()
        store = mock.Mock(spec=CacheStore)
        store.load.return_value = {"sessions": [selected], "errors": []}
        for retv, notice in (("2", "Custom input is disabled"), ("3", "Deletion is disabled")):
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                run_rofi(
                    {
                        "ROFI_RETV": retv,
                        "ROFI_INFO": json.dumps(selected),
                    },
                    store=store,
                    config=self._config(),
                )
            self.assertIn(notice, output.getvalue())
            self.assertIn("hello", output.getvalue())
            self.assertIn("\x00keep-selection\x1ftrue", output.getvalue())
            self.assertIn("\x00keep-filter\x1ftrue", output.getvalue())

    def test_selection_requires_the_contract_lifecycle(self) -> None:
        from rofi_agent_plus.rofi import _open_selection

        with self.assertRaisesRegex(engine.PickerError, "prepared authority"):
            _open_selection(session(), PickerConfig())
        for name in ("resolve_open_target", "launch_attach", "focus_existing_window"):
            self.assertFalse(hasattr(engine, name), name)

    @staticmethod
    def _config() -> PickerConfig:
        return PickerConfig()


class EntrypointTest(unittest.TestCase):
    def test_rofi_selection_argv_is_not_parsed_as_a_diagnostic_command(self) -> None:
        with (
            mock.patch.dict(os.environ, {"ROFI_RETV": "1"}, clear=False),
            mock.patch("rofi_agent_plus.app.run_rofi", return_value=0) as rofi_mode,
        ):
            self.assertEqual(0, app.main(["visible row text"]))
        rofi_mode.assert_called_once()

    def test_direct_and_symlink_entrypoints_show_help_without_bytecode(self) -> None:
        root = Path(__file__).resolve().parents[1]
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        direct = subprocess.run(
            [str(root / "bin" / "rofi-agent-plus"), "--help"],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        self.assertEqual(0, direct.returncode)
        self.assertIn("{list,active,refresh}", direct.stdout)
        self.assertNotIn("open-opencode", direct.stdout)
        with tempfile.TemporaryDirectory() as temporary:
            link = Path(temporary) / "picker"
            link.symlink_to(root / "bin" / "rofi-agent-plus")
            linked = subprocess.run(
                [str(link), "--help"],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )
        self.assertEqual(0, linked.returncode)
        self.assertFalse(any(root.rglob("__pycache__")))

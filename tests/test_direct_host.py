from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from rofi_agent_plus.config import load_direct_config
from rofi_agent_plus.host import (
    HostError,
    build_inventory,
    enter_reference,
    validate_entry_reference,
)
from rofi_agent_plus.host import (
    main as host_main,
)
from rofi_agent_plus.host_mesh import HostMeshError, resolve_self_mesh
from rofi_agent_plus.model import (
    EntryReference,
    RecordError,
    UserScope,
    decode_document_json,
    decode_reference_json,
    parse_entry_reference,
    parse_inventory,
)
from rofi_agent_plus.process import ProcessResult
from rofi_agent_plus.providers import claude, codex

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "direct-host"
MESH_REVISION = "sha256:" + "a" * 64


class HostHarness:
    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.home.mkdir()
        self.bin = root / "bin"
        self.bin.mkdir()
        self.cache = root / "cache"
        self.codex_home = self.home / ".codex"
        self.codex_home.mkdir()
        self.work_home = self.home / ".codex-work"
        self.work_home.mkdir()
        (self.work_home / "config.toml").write_text('model = "native-default"\n', encoding="utf-8")
        self.claude_home = self.home / ".claude"
        self.claude_home.mkdir()
        self.codex = self.executable("codex")
        self.claude = self.executable("claude")
        self.mesh = self.executable("rofi-ssh-plus")
        self.user = UserScope(1000, "bryan")
        self.env = {
            "PATH": str(self.bin),
            "HOME": "/not-the-owning-home",
            "XDG_CACHE_HOME": str(self.cache),
            "CODEX_PLUS_SOCKET": "/tmp/foreign-codex.sock",
        }
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def executable(self, name: str) -> Path:
        path = self.bin / name
        path.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def mesh_payload(self, host_id: str = "snap") -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "generatedAt": 1792540800000,
            "meshRevision": MESH_REVISION,
            "localHostId": host_id,
            "sshPolicy": {
                "executable": "ssh",
                "connectTimeoutSeconds": 10,
                "connectionAttempts": 2,
                "routeHealthTtlSeconds": 300,
            },
            "hosts": [
                {
                    "id": host_id,
                    "display": "Snap logical host",
                    "local": True,
                    "aliases": ["OS80H1VV3", "snap.example"],
                    "routes": [],
                }
            ],
        }

    def runner(self, argv: tuple[str, ...], **kwargs: Any) -> ProcessResult:
        self.calls.append((tuple(argv), dict(kwargs)))
        if tuple(argv[1:]) == ("mesh", "list", "--json"):
            raw = json.dumps(self.mesh_payload(), separators=(",", ":")).encode() + b"\n"
            return ProcessResult(tuple(argv), 0, raw, b"")
        name = Path(argv[0]).name
        tail = tuple(argv[1:])
        if tail == ("agents", "--help"):
            return ProcessResult(tuple(argv), 0, b"usage: agents\n", b"")
        if name == "codex" and tail == ("app-server", "daemon", "version"):
            if kwargs.get("env", {}).get("CODEX_HOME") == str(self.work_home):
                return ProcessResult(tuple(argv), 13, b"", b"permission denied reading endpoint")
            return ProcessResult(
                tuple(argv),
                1,
                b"",
                b"failed to connect to daemon socket: No such file or directory",
            )
        if name == "claude" and tail == ("daemon", "status"):
            return ProcessResult(tuple(argv), 0, b"pid:     42\n  control.sock: reachable\n", b"")
        raise AssertionError(f"unexpected provider command: {argv!r}")

    def write_config(self, declarations: list[dict[str, str]]) -> Path:
        config = self.root / "direct.toml"
        lines = ["schema_version = 1", "", "[desktop]", 'terminal = ["kitty"]', ""]
        for declaration in declarations:
            lines.append("[[environments]]")
            for key, value in declaration.items():
                lines.append(f"{key} = {json.dumps(value)}")
            lines.append("")
        config.write_text("\n".join(lines), encoding="utf-8")
        return config

    def inventory(self, config_path: Path | None = None, **kwargs: Any):
        return build_inventory(
            config_path,
            env=kwargs.pop("env", self.env),
            runner=kwargs.pop("runner", self.runner),
            user_scope=kwargs.pop("user_scope", self.user),
            user_home=kwargs.pop("user_home", self.home),
            mesh_executable=kwargs.pop("mesh_executable", str(self.mesh)),
            **kwargs,
        )


class DirectHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.harness = HostHarness(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_native_status_requires_recognized_positive_evidence(self) -> None:
        current_codex = {"status": "running", "backend": "pid", "appServerVersion": "0.163.0"}
        self.assertEqual(
            codex.daemon_observation(
                ProcessResult(("codex",), 0, json.dumps(current_codex).encode(), b"")
            ).status,
            "running",
        )
        for value in ({}, {"error": "not connected"}, {"status": "stopped"}):
            self.assertEqual(
                codex.daemon_observation(
                    ProcessResult(("codex",), 0, json.dumps(value).encode(), b"")
                ).status,
                "unknown",
            )
        self.assertEqual(
            codex.daemon_observation(
                ProcessResult(("codex",), 1, b"", b"daemon socket: connection refused")
            ).status,
            "unknown",
        )
        self.assertEqual(
            claude.daemon_observation(
                ProcessResult(("claude",), 0, b"workers: 6 running\n", b"")
            ).status,
            "unknown",
        )
        self.assertEqual(
            claude.daemon_observation(
                ProcessResult(("claude",), 0, b"pid: 42\ncontrol.sock: unreachable\n", b"")
            ).status,
            "unknown",
        )
        self.assertEqual(
            claude.daemon_observation(
                ProcessResult(("claude",), 0, b"pid: 42\ncontrol.sock: reachable\n", b"")
            ).status,
            "running",
        )

    def test_strict_versioned_fixtures_parse(self) -> None:
        inventory = parse_inventory(
            decode_document_json((FIXTURE_ROOT / "inventory.json").read_bytes())
        )
        reference = parse_entry_reference(
            decode_document_json((FIXTURE_ROOT / "entry-reference.json").read_bytes())
        )
        self.assertEqual(inventory.host_id, "snap")
        self.assertEqual(reference.target_key.provider, "codex")
        with self.assertRaises(RecordError):
            parse_inventory(
                decode_document_json((FIXTURE_ROOT / "invalid-unknown-version.json").read_bytes())
            )

    def test_source_host_cli_emits_inventory_and_forwards_explicit_config(self) -> None:
        inventory = parse_inventory(
            decode_document_json((FIXTURE_ROOT / "inventory.json").read_bytes())
        )
        config_path = self.root / "private-direct.toml"
        output = io.StringIO()
        with (
            patch("rofi_agent_plus.host.build_inventory", return_value=inventory) as build,
            patch("rofi_agent_plus.host.sys.stdout", output),
        ):
            self.assertEqual(host_main(["inventory", "--json", "--config", str(config_path)]), 0)
        self.assertEqual(build.call_args.args, (config_path,))
        self.assertEqual(json.loads(output.getvalue()), inventory.to_json())

        reference = (FIXTURE_ROOT / "entry-reference.json").read_text(encoding="utf-8").strip()
        output = io.StringIO()
        with (
            patch("rofi_agent_plus.host.enter_reference", return_value=None) as enter,
            patch("rofi_agent_plus.host.sys.stdout", output),
        ):
            self.assertEqual(
                host_main(["enter", "--reference-json", reference, "--config", str(config_path)]),
                0,
            )
        self.assertEqual(enter.call_args.args, (reference, config_path))

    def test_wire_rejects_duplicate_keys_unknown_versions_and_unsafe_reference(self) -> None:
        with self.assertRaises(RecordError):
            decode_document_json(b'{"schemaVersion":1,"schemaVersion":1}\n')
        deeply_nested = '{"schemaVersion":1,"unknown":' + "[" * 1200 + "0" + "]" * 1200 + "}"
        with self.assertRaises(RecordError):
            decode_reference_json(deeply_nested)
        inventory = json.loads((FIXTURE_ROOT / "inventory.json").read_text(encoding="utf-8"))
        inventory["errors"] = [
            {
                "code": "probe_failed",
                "stage": "capability",
                "message": "probe returned an unknown diagnostic detail",
                "diagnosticDetail": {"attempt": 2},
            }
        ]
        parse_inventory(inventory)
        inventory["unexpected"] = True
        with self.assertRaises(RecordError):
            parse_inventory(inventory)
        reference = json.loads((FIXTURE_ROOT / "entry-reference.json").read_text(encoding="utf-8"))
        reference["targetKey"]["executablePath"] = "/tmp/attacker"
        with self.assertRaises(RecordError):
            parse_entry_reference(reference)
        reference.pop("targetKey")
        reference["schemaVersion"] = 9
        with self.assertRaises(RecordError):
            parse_entry_reference(reference)

    def test_configuration_uses_direct_schema_and_isolates_bad_rows(self) -> None:
        config = self.harness.write_config(
            [
                {"provider": "codex", "home": "$HOME/.codex-work"},
                {"provider": "claude", "home": "~/.claude-extra", "label": "Extra"},
            ]
        )
        loaded = load_direct_config(config, user_home=self.harness.home)
        self.assertEqual(len(loaded.issues), 1)
        self.assertEqual([item.provider for item in loaded.environments], ["claude"])
        self.assertEqual(loaded.desktop.terminal, ("kitty",))
        xdg_config = self.root / "xdg-config"
        legacy_dir = xdg_config / "rofi-agent-plus"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "config.toml").write_text(
            "max_sessions = 40\nrefresh_seconds = 30\n", encoding="utf-8"
        )
        fresh = load_direct_config(
            env={"XDG_CONFIG_HOME": str(xdg_config)}, user_home=self.harness.home
        )
        self.assertEqual(fresh.environments, ())

    def test_inventory_deduplicates_canonical_aliases_and_preserves_partial_targets(self) -> None:
        alias = self.harness.home / ".codex-alias"
        alias.symlink_to(self.harness.codex_home, target_is_directory=True)
        config = self.harness.write_config(
            [
                {"provider": "codex", "home": "~/.codex", "label": "Personal"},
                {"provider": "codex", "home": str(alias), "label": "Personal", "id": "same-target"},
                {"provider": "codex", "home": "~/.codex-work", "label": "Work"},
                {"provider": "codex", "home": "~/.codex-missing", "label": "Missing"},
            ]
        )
        inventory = self.harness.inventory(config, observed_at=1792540800000)
        codex_targets = [target for target in inventory.targets if target.key.provider == "codex"]
        self.assertEqual(len(codex_targets), 3)
        self.assertEqual(len({target.key.identity for target in codex_targets}), 3)
        by_home = {target.key.home: target for target in codex_targets}
        self.assertEqual(by_home[str(self.harness.codex_home)].label, "Personal")
        self.assertEqual(by_home[str(self.harness.codex_home)].daemon.status, "stopped")
        self.assertEqual(by_home[str(self.harness.work_home)].daemon.status, "unknown")
        self.assertEqual(by_home[str(self.harness.work_home)].capability.status, "supported")
        self.assertEqual(
            by_home[str(self.harness.home / ".codex-missing")].capability.status, "unavailable"
        )
        self.assertFalse((self.harness.home / ".codex-missing").exists())
        claude = next(target for target in inventory.targets if target.key.provider == "claude")
        self.assertEqual(claude.daemon.status, "running")
        self.assertEqual(inventory.host_id, "snap")
        self.assertEqual(inventory.provider_coverage[0].status, "partial")
        self.assertEqual(inventory.provider_coverage[1].status, "complete")
        codex_status = [
            call
            for call in self.harness.calls
            if call[0][1:] == ("app-server", "daemon", "version")
        ]
        self.assertEqual(len(codex_status), 2)
        self.assertTrue(all("CODEX_PLUS_SOCKET" not in call[1]["env"] for call in codex_status))
        self.assertTrue(all(call[0][1:] != ("login",) for call in self.harness.calls))

    def test_capability_probe_is_cached_by_executable_and_status_is_not(self) -> None:
        config = self.harness.write_config(
            [{"provider": "codex", "home": "~/.codex-work", "label": "Work"}]
        )
        self.harness.inventory(config)
        first_help_count = sum(call[0][1:] == ("agents", "--help") for call in self.harness.calls)
        first_status_count = sum(
            call[0][1:] == ("app-server", "daemon", "version") for call in self.harness.calls
        )
        self.harness.inventory(config)
        second_help_count = sum(call[0][1:] == ("agents", "--help") for call in self.harness.calls)
        second_status_count = sum(
            call[0][1:] == ("app-server", "daemon", "version") for call in self.harness.calls
        )
        self.assertEqual(first_help_count, 2)
        self.assertEqual(second_help_count, first_help_count)
        self.assertEqual(second_status_count, first_status_count * 2)
        self.assertTrue(
            (
                self.harness.cache / "rofi-agent-plus" / "direct-v1" / "provider-capabilities.json"
            ).exists()
        )

    def test_absent_undeclared_provider_is_quiet_and_never_probed(self) -> None:
        self.harness.claude.unlink()
        with patch.object(claude, "capability_argv", side_effect=AssertionError("Claude probed")):
            with patch.object(claude, "status_argv", side_effect=AssertionError("Claude probed")):
                inventory = self.harness.inventory()
        self.assertEqual([target.key.provider for target in inventory.targets], ["codex"])
        self.assertEqual(inventory.targets[0].label, "Codex · Default")
        self.assertEqual(inventory.errors, ())
        self.assertEqual(
            {coverage.provider: coverage.status for coverage in inventory.provider_coverage},
            {"codex": "complete", "claude": "missing"},
        )
        config = self.harness.write_config([{"provider": "codex", "home": "~/.codex"}])
        self.assertEqual(self.harness.inventory(config).targets[0].label, "Codex · Default")

    def test_explicit_missing_provider_declaration_still_reports_configuration_problem(
        self,
    ) -> None:
        self.harness.claude.unlink()
        config = self.harness.write_config([{"provider": "claude", "home": "~/.claude"}])
        inventory = self.harness.inventory(config)
        self.assertTrue(
            any(
                error.provider == "claude" and error.code == "provider_executable_missing"
                for error in inventory.errors
            )
        )

    def test_missing_provider_unsupported_capability_and_status_failure_are_distinct(self) -> None:
        env = dict(self.harness.env)
        only_codex_bin = self.root / "codex-only"
        only_codex_bin.mkdir()
        codex = only_codex_bin / "codex"
        codex.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        codex.chmod(0o755)

        def runner(argv: tuple[str, ...], **kwargs: Any) -> ProcessResult:
            if tuple(argv[1:]) == ("mesh", "list", "--json"):
                raw = json.dumps(self.harness.mesh_payload()).encode() + b"\n"
                return ProcessResult(tuple(argv), 0, raw, b"")
            if tuple(argv[1:]) == ("agents", "--help"):
                return ProcessResult(tuple(argv), 7, b"", b"unknown command: agents")
            raise AssertionError(argv)

        env["PATH"] = str(only_codex_bin)
        inventory = self.harness.inventory(env=env, runner=runner)
        coverage = {item.provider: item for item in inventory.provider_coverage}
        self.assertEqual(coverage["codex"].status, "unsupported")
        self.assertEqual(coverage["claude"].status, "missing")
        self.assertTrue(all(target.daemon.status == "unknown" for target in inventory.targets))

    def test_bad_status_exit_is_unknown_and_inventory_never_starts_a_daemon(self) -> None:
        def runner(argv: tuple[str, ...], **kwargs: Any) -> ProcessResult:
            self.harness.calls.append((tuple(argv), dict(kwargs)))
            if tuple(argv[1:]) == ("mesh", "list", "--json"):
                raw = json.dumps(self.harness.mesh_payload()).encode() + b"\n"
                return ProcessResult(tuple(argv), 0, raw, b"")
            if tuple(argv[1:]) == ("agents", "--help"):
                return ProcessResult(tuple(argv), 0, b"usage: agents\n", b"")
            if tuple(argv[1:]) == ("app-server", "daemon", "version"):
                return ProcessResult(tuple(argv), 1, b"", b"permission denied")
            if tuple(argv[1:]) == ("daemon", "status"):
                return ProcessResult(tuple(argv), 1, b"", b"temporary service error")
            raise AssertionError(f"inventory attempted a non-passive command: {argv!r}")

        inventory = self.harness.inventory(runner=runner)
        self.assertTrue(all(target.daemon.status == "unknown" for target in inventory.targets))
        self.assertTrue(
            all("start" not in call[0] and "login" not in call[0] for call in self.harness.calls)
        )

    def test_configured_mesh_failure_never_falls_back_to_os_hostname(self) -> None:
        def failed_runner(argv: tuple[str, ...], **kwargs: Any) -> ProcessResult:
            return ProcessResult(tuple(argv), 1, b"", b"configuration failure")

        with (
            patch("rofi_agent_plus.host_mesh.socket.gethostname", return_value="os80h1vv3"),
            self.assertRaises(HostError) as raised,
        ):
            self.harness.inventory(runner=failed_runner)
        self.assertEqual(raised.exception.code, "mesh_unavailable")

    def test_self_identity_comes_from_mesh_logical_host_even_if_os_name_differs(self) -> None:
        with (
            patch("rofi_agent_plus.host_mesh.socket.gethostname", return_value="OS80H1VV3"),
            patch("rofi_agent_plus.host_mesh.socket.getfqdn", return_value="OS80H1VV3.example"),
        ):
            inventory = self.harness.inventory(mesh_executable=None)
        self.assertEqual(inventory.host_id, "snap")
        self.assertEqual(inventory.targets[0].key.host_id, "snap")
        mesh_call = next(
            call for call in self.harness.calls if call[0][1:] == ("mesh", "list", "--json")
        )
        self.assertEqual(mesh_call[1]["env"], self.harness.env)

    def test_entry_revalidates_host_user_and_spec_and_ignores_label_changes(self) -> None:
        config = self.harness.write_config(
            [{"provider": "codex", "home": "~/.codex", "label": "First label"}]
        )
        inventory = self.harness.inventory(config, include_status=False)
        target = next(item for item in inventory.targets if item.key.provider == "codex")
        reference = EntryReference(
            inventory.host_id, inventory.user, target.key, target.spec_revision
        )

        config.write_text(
            'schema_version = 1\n[[environments]]\nprovider = "codex"\nhome = "~/.codex"\nlabel = "Changed label"\n',
            encoding="utf-8",
        )
        accepted = validate_entry_reference(
            reference.to_json(),
            config,
            env=self.harness.env,
            runner=self.harness.runner,
            user_scope=self.harness.user,
            user_home=self.harness.home,
            mesh_executable=str(self.harness.mesh),
            observed_at=1792540800000,
        )
        self.assertEqual(accepted.target.label, "Changed label")
        with self.assertRaises(HostError) as wrong_host:
            wrong = reference.to_json()
            wrong["expectedHostId"] = "other"
            wrong["targetKey"]["hostId"] = "other"
            validate_entry_reference(
                wrong,
                config,
                env=self.harness.env,
                runner=self.harness.runner,
                user_scope=self.harness.user,
                user_home=self.harness.home,
                mesh_executable=str(self.harness.mesh),
            )
        self.assertEqual(wrong_host.exception.code, "wrong_host")
        with self.assertRaises(HostError) as wrong_user:
            wrong = reference.to_json()
            wrong["expectedUser"]["uid"] = 1001
            wrong["expectedUser"]["account"] = "other"
            wrong["targetKey"]["uid"] = 1001
            wrong["targetKey"]["account"] = "other"
            validate_entry_reference(
                wrong,
                config,
                env=self.harness.env,
                runner=self.harness.runner,
                user_scope=self.harness.user,
                user_home=self.harness.home,
                mesh_executable=str(self.harness.mesh),
            )
        self.assertEqual(wrong_user.exception.code, "wrong_user")
        stale = reference.to_json()
        stale["specRevision"] = "sha256:" + "9" * 64
        with self.assertRaises(HostError) as stale_spec:
            validate_entry_reference(
                stale,
                config,
                env=self.harness.env,
                runner=self.harness.runner,
                user_scope=self.harness.user,
                user_home=self.harness.home,
                mesh_executable=str(self.harness.mesh),
            )
        self.assertEqual(stale_spec.exception.code, "stale_specification")

    def test_entry_uses_selected_home_raw_executable_home_cwd_and_real_tty(self) -> None:
        config = self.harness.write_config(
            [{"provider": "codex", "home": "~/.codex-work", "label": "Work"}]
        )
        parent_env = dict(self.harness.env)
        parent_env["CODEX_HOME"] = "/wrong/parent/home"
        parent_env["CODEX_PLUS_SOCKET"] = "/wrong/parent/socket"
        parent_env["CLAUDE_CONFIG_DIR"] = "/preserve/native/claude"
        parent_env["CODEX_NATIVE_HOOK"] = "keep"
        inventory = self.harness.inventory(config, env=parent_env, include_status=False)
        catalog_homes = {target.key.home for target in inventory.targets}
        self.assertIn(str(self.harness.codex_home), catalog_homes)
        self.assertIn(str(self.harness.work_home), catalog_homes)
        self.assertIn(str(self.harness.claude_home), catalog_homes)
        self.assertNotIn(parent_env["CODEX_HOME"], catalog_homes)
        self.assertNotIn(parent_env["CLAUDE_CONFIG_DIR"], catalog_homes)
        work = next(
            target for target in inventory.targets if target.key.home == str(self.harness.work_home)
        )
        reference = EntryReference(inventory.host_id, inventory.user, work.key, work.spec_revision)
        events: list[tuple[object, ...]] = []

        plan = enter_reference(
            json.dumps(reference.to_json()),
            config,
            env=parent_env,
            runner=self.harness.runner,
            user_scope=self.harness.user,
            user_home=self.harness.home,
            mesh_executable=str(self.harness.mesh),
            is_tty=True,
            chdir=lambda cwd: events.append(("chdir", cwd)),
            execve=lambda path, argv, environment: events.append(
                ("exec", path, tuple(argv), dict(environment))
            ),
        )
        self.assertIsNotNone(plan)
        self.assertEqual(events[0], ("chdir", str(self.harness.home)))
        _kind, executable, argv, entry_env = events[1]
        self.assertEqual(executable, str(self.harness.codex.resolve()))
        self.assertEqual(argv, (str(self.harness.codex.resolve()), "agents"))
        self.assertEqual(entry_env["CODEX_HOME"], str(self.harness.work_home))
        self.assertNotIn("CODEX_PLUS_SOCKET", entry_env)
        self.assertEqual(entry_env["CLAUDE_CONFIG_DIR"], "/preserve/native/claude")
        self.assertEqual(entry_env["CODEX_NATIVE_HOOK"], "keep")
        self.assertFalse(
            any(call[0][1:] == ("app-server", "daemon", "version") for call in self.harness.calls)
        )
        with self.assertRaises(HostError) as no_tty:
            enter_reference(
                json.dumps(reference.to_json()),
                config,
                env=parent_env,
                runner=self.harness.runner,
                user_scope=self.harness.user,
                user_home=self.harness.home,
                mesh_executable=str(self.harness.mesh),
                is_tty=False,
            )
        self.assertEqual(no_tty.exception.code, "tty_required")

    def test_additional_codex_home_requires_readable_config_toml(self) -> None:
        config = self.harness.write_config(
            [{"provider": "codex", "home": "~/.codex-unprepared", "label": "Unprepared"}]
        )
        inventory = self.harness.inventory(config)
        target = next(
            item for item in inventory.targets if item.key.home.endswith(".codex-unprepared")
        )
        self.assertEqual(target.capability.status, "unavailable")
        self.assertEqual(target.capability.reason, "home_missing")
        self.assertFalse(Path(target.key.home).exists())


class HostMeshTests(unittest.TestCase):
    def test_absent_cli_uses_local_identity_but_configured_failure_does_not(self) -> None:
        with (
            patch("rofi_agent_plus.host_mesh.socket.gethostname", return_value="snap"),
        ):
            fallback = resolve_self_mesh(None, runner=lambda *_args, **_kwargs: self.fail())
        self.assertEqual(fallback.local.host_id, "snap")
        with self.assertRaises(HostMeshError) as raised:
            resolve_self_mesh(
                "/configured/rofi-ssh-plus",
                runner=lambda argv, **kwargs: ProcessResult(tuple(argv), 1, b"", b"failed"),
            )
        self.assertEqual(raised.exception.code, "mesh_unavailable")


if __name__ == "__main__":
    unittest.main()

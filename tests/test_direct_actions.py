from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rofi_agent_plus.actions import ActionError, Actions
from rofi_agent_plus.desktop import Desktop, DesktopError, Window, observe_process
from rofi_agent_plus.model import EntryReference, Target, parse_entry_reference, parse_inventory
from rofi_agent_plus.registry import Registry
from rofi_agent_plus.transport import PinnedTransport
from rofi_agent_plus.viewer import run_viewer

FIXTURES = Path(__file__).parent / "fixtures" / "direct-host"


class FakeDesktop:
    def __init__(self, generation: str, windows=()):
        self.generation = generation
        self.env = {"PATH": "/usr/bin:/bin"}
        self.visible = tuple(windows)
        self.focused: list[int] = []

    def windows(self):
        return self.visible

    @staticmethod
    def match(windows, marker, terminal):
        return Desktop.match(windows, marker, terminal)

    def focus(self, window_id, marker, terminal):
        current = self.match(self.windows(), marker, terminal)
        if current is None or current.window_id != window_id:
            raise RuntimeError("window disappeared")
        self.focused.append(window_id)


class FakeTransport:
    def __init__(self):
        self.calls = 0
        self.pin = PinnedTransport(
            "local",
            "snap",
            "sha256:" + "a" * 64,
            helper="/usr/bin/rofi-agent-plus-host",
        )

    def pin_entry(self, host_id):
        self.calls += 1
        if host_id != "snap":
            raise AssertionError(host_id)
        return self.pin


class DirectActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.generation = "a" * 64
        self.registry = Registry(self.generation, self.root / "registry")
        self.inventory = parse_inventory(
            json.loads((FIXTURES / "inventory.json").read_text(encoding="utf-8"))
        )
        self.target: Target = self.inventory.targets[0]
        self.reference: EntryReference = parse_entry_reference(
            json.loads((FIXTURES / "entry-reference.json").read_text(encoding="utf-8"))
        )
        self.desktop = FakeDesktop(self.generation)
        self.transport = FakeTransport()
        self.popen_calls: list[tuple[list[str], dict[str, object]]] = []

    def action(self, *, popen=None) -> Actions:
        def launch(argv, **kwargs):
            self.popen_calls.append((list(argv), dict(kwargs)))
            return SimpleNamespace(pid=os.getpid())

        return Actions(
            self.desktop,
            self.registry,
            self.transport,
            terminal=("kitty",),
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "/foreign"},
            popen=launch if popen is None else popen,
        )

    def test_open_dispatches_once_and_reuses_pending_instance_without_mesh_probe(self):
        action = self.action()
        with (
            patch("rofi_agent_plus.desktop.shutil.which", return_value="/usr/bin/kitty"),
            patch(
                "rofi_agent_plus.actions._artifact_root",
                return_value=Path(__file__).resolve().parents[1],
            ),
        ):
            first = action.open_target(self.target, self.reference, request_id="1" * 32)
            second = action.open_target(self.target, self.reference, request_id="2" * 32)
        self.assertEqual(first.instance_id, second.instance_id)
        self.assertEqual(len(self.popen_calls), 1)
        self.assertEqual(self.transport.calls, 1)
        self.assertEqual(first.state, "dispatching")
        self.assertEqual(first.process("terminal"), observe_process(os.getpid()))
        argv, options = self.popen_calls[0]
        self.assertEqual(argv[0], "/usr/bin/kitty")
        self.assertIn("--single-instance=no", argv)
        self.assertIn("allow_remote_control=socket-only", argv)
        self.assertTrue(any(item.endswith("/rofi-agent-plus-viewer") for item in argv))
        self.assertEqual(options["start_new_session"], True)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["env"]["PYTHONPATH"], "/foreign")
        self.assertEqual(Path(options["cwd"]), Path.home())

    def test_explicit_additional_request_creates_a_second_instance(self):
        action = self.action()
        with patch("rofi_agent_plus.desktop.shutil.which", return_value="/usr/bin/kitty"):
            first = action.open_target(self.target, self.reference, request_id="1" * 32)
            second = action.open_target(
                self.target, self.reference, additional=True, request_id="2" * 32
            )
        self.assertNotEqual(first.instance_id, second.instance_id)
        self.assertEqual([first.value["ordinal"], second.value["ordinal"]], [1, 2])
        self.assertEqual(len(self.popen_calls), 2)

    def test_failed_spawn_is_retired_and_request_replay_never_spawns_again(self):
        def fail(*_argv, **_kwargs):
            raise OSError("controlled spawn failure")

        action = self.action(popen=fail)
        with patch("rofi_agent_plus.desktop.shutil.which", return_value="/usr/bin/kitty"):
            with self.assertRaises(ActionError) as raised:
                action.open_target(self.target, self.reference, request_id="3" * 32)
            with self.assertRaises(ActionError) as replay:
                action.open_target(self.target, self.reference, request_id="3" * 32)
        self.assertEqual(raised.exception.code, "terminal_spawn_failed")
        self.assertEqual(replay.exception.code, "terminal_spawn_failed")
        self.assertEqual(self.transport.calls, 1)

    def test_terminal_ack_merge_preserves_newer_viewer_lifecycle_state(self):
        target_id = Actions.target_id(self.target)

        def launch(argv, **kwargs):
            parts = list(argv)
            instance_id = parts[parts.index("--instance-id") + 1]
            self.registry.update(
                target_id,
                instance_id,
                state="failed",
                error={"code": "viewer_raced", "message": "viewer exited"},
            )
            return SimpleNamespace(pid=os.getpid())

        action = self.action(popen=launch)
        with patch("rofi_agent_plus.desktop.shutil.which", return_value="/usr/bin/kitty"):
            with self.assertRaises(ActionError) as raised:
                action.open_target(self.target, self.reference, request_id="7" * 32)
        self.assertEqual(raised.exception.code, "viewer_raced")
        record = self.registry.find_request(target_id, "7" * 32)
        self.assertEqual(record.state, "failed")
        self.assertEqual(record.value["error"]["code"], "viewer_raced")
        self.assertEqual(record.process("terminal"), observe_process(os.getpid()))

    def test_stale_reference_is_rejected_before_transport_or_spawn(self):
        wrong = EntryReference(
            self.reference.expected_host_id,
            self.reference.expected_user,
            self.inventory.targets[1].key,
            self.reference.spec_revision,
        )
        action = self.action()
        with self.assertRaisesRegex(ActionError, "do not match"):
            action.open_target(self.target, wrong)
        self.assertEqual(self.transport.calls, 0)
        self.assertEqual(self.popen_calls, [])

    def _live_instance(self):
        target_id = Actions.target_id(self.target)
        current = observe_process(os.getpid())
        record, _ = self.registry.reserve(
            target_id=target_id,
            target=self.target.wire,
            reference=self.reference.wire,
            request_id="4" * 32,
            additional=False,
            owner=current,
            transport=FakeTransport().pin.to_json(),
        )
        self.registry.update(
            target_id,
            record.instance_id,
            state="live",
            terminal=current.wire(),
            wrapper=current.wire(),
            client=current.wire(),
        )
        self.desktop.visible = (Window(42, current.pid, record.marker, "dynamic title", True),)
        return target_id, record

    def test_focus_revalidates_private_kitty_layout_without_transport_probe(self):
        target_id, record = self._live_instance()
        action = self.action()
        with patch("rofi_agent_plus.actions.validate_kitty_layout") as validate:
            focused = action.focus_instance(target_id, record.instance_id)
        self.assertEqual(focused.instance_id, record.instance_id)
        self.assertEqual(self.desktop.focused, [42])
        validate.assert_called_once()
        self.assertEqual(self.transport.calls, 0)

    def test_ambiguous_private_layout_marks_instance_uncertain_and_never_launches(self):
        target_id, record = self._live_instance()
        action = self.action()
        with (
            patch(
                "rofi_agent_plus.actions.validate_kitty_layout",
                side_effect=DesktopError("ambiguous_terminal_layout"),
            ),
            self.assertRaises(ActionError),
        ):
            action.open_target(self.target, self.reference)
        current = self.registry.records(target_id)[0]
        self.assertEqual(current.state, "uncertain")
        self.assertEqual(self.popen_calls, [])

    def _reserve_viewer(self, helper: Path, request: str = "5" * 32):
        target_id = Actions.target_id(self.target)
        owner = observe_process(os.getpid())
        pinned = PinnedTransport(
            "local",
            "snap",
            "sha256:" + "a" * 64,
            helper=str(helper),
        )
        record, created = self.registry.reserve(
            target_id=target_id,
            target=self.target.wire,
            reference=self.reference.wire,
            request_id=request,
            additional=True,
            owner=owner,
            transport=pinned.to_json(),
        )
        self.assertTrue(created)
        self.registry.update(target_id, record.instance_id, state="dispatching")
        return target_id, record

    def _write_helper(self, path: Path, *, exit_code: int = 0):
        captured = self.root / (path.name + ".json")
        path.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys, time\n"
            "import signal\n"
            f"pathlib.Path({str(captured)!r}).write_text(json.dumps({{'argv':sys.argv[1:],'cwd':os.getcwd(),'env':dict(os.environ),'pgrp':os.getpgrp(),'sigint':str(signal.getsignal(signal.SIGINT))}}))\n"
            "time.sleep(0.05)\n"
            f"raise SystemExit({exit_code})\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return captured

    def test_viewer_acknowledges_wrapper_tracks_direct_child_and_retires(self):
        helper = self.root / "native helper"
        captured = self._write_helper(helper)
        target_id, record = self._reserve_viewer(helper)
        errors = io.StringIO()
        called = {}

        def popen(argv, **kwargs):
            called["argv"] = list(argv)
            called["kwargs"] = kwargs
            called["sigint"] = signal.getsignal(signal.SIGINT)
            return subprocess.Popen(argv, **kwargs)

        result = run_viewer(
            self.registry.root,
            self.generation,
            target_id,
            record.instance_id,
            env={"PATH": "/usr/bin:/bin", "CODEX_HOME": "/foreign", "MARKER": "present"},
            popen=popen,
            stdin=io.StringIO(),
            stderr=errors,
            is_tty=False,
        )
        self.assertEqual(result, 0, errors.getvalue())
        self.assertTrue(callable(called["sigint"]))
        self.assertIsNot(called["sigint"], signal.SIG_IGN)
        self.assertFalse(called["kwargs"]["start_new_session"])
        child = json.loads(captured.read_text())
        self.assertEqual(child["argv"][0], "enter")
        self.assertIn("--reference-json", child["argv"])
        self.assertEqual(child["env"]["MARKER"], "present")
        self.assertEqual(Path(child["cwd"]), Path.home())
        self.assertEqual(child["pgrp"], os.getpgrp())
        self.assertIn("default_int_handler", child["sigint"])
        final = self.registry.find_request(target_id, record.value["requestId"])
        self.assertEqual(final.state, "exited")
        self.assertEqual(final.process("wrapper"), observe_process(os.getpid()))
        self.assertIsNotNone(final.process("client"))

    def test_viewer_failure_is_recorded_before_diagnostic_hold(self):
        helper = self.root / "failing native helper"
        self._write_helper(helper, exit_code=7)
        target_id, record = self._reserve_viewer(helper, "6" * 32)
        errors = io.StringIO()
        result = run_viewer(
            self.registry.root,
            self.generation,
            target_id,
            record.instance_id,
            env={"PATH": "/usr/bin:/bin"},
            stdin=io.StringIO("\n"),
            stderr=errors,
            is_tty=True,
        )
        final = self.registry.find_request(target_id, record.value["requestId"])
        self.assertEqual(result, 1)
        self.assertEqual(final.state, "failed")
        self.assertEqual(final.value["error"]["code"], "client_exit_status")
        self.assertIn("status 7", errors.getvalue())
        self.assertIn("Press Enter", errors.getvalue())

    def test_source_wrappers_ignore_poisoned_cwd_and_pythonpath(self):
        checkout = Path(__file__).resolve().parents[1]
        poison = self.root / "poisoned-cwd"
        package = poison / "rofi_agent_plus"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("raise RuntimeError('poison package imported')\n")
        env = dict(os.environ)
        env["PYTHONPATH"] = f"{poison}{os.pathsep}{checkout}"
        for name in ("rofi-agent-plus-host", "rofi-agent-plus-viewer"):
            completed = subprocess.run(
                [sys.executable, str(checkout / "bin" / name), "--help"],
                cwd=poison,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("usage:", completed.stdout)

    def test_installed_viewer_uses_isolated_same_interpreter_module(self):
        target_id, record = self._live_instance()
        action = self.action()
        with patch("rofi_agent_plus.actions._artifact_root", return_value=self.root):
            argv = action._viewer_argv(target_id, record)
        self.assertEqual(
            argv[:4],
            [sys.executable, "-I", "-m", "rofi_agent_plus.viewer"],
        )


if __name__ == "__main__":
    unittest.main()

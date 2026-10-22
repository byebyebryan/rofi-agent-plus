from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rofi_agent_plus.host_mesh import Mesh, MeshHost, Route, local_only_mesh
from rofi_agent_plus.process import ProcessError, ProcessResult
from rofi_agent_plus.transport import PinnedTransport, Transport, TransportError

FIXTURES = Path(__file__).parent / "fixtures" / "direct-host"


class TransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.path = self.bin / "ssh"
        self.path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.path.chmod(0o755)
        self.local_inventory = json.loads((FIXTURES / "inventory.json").read_text())
        self.remote_inventory = json.loads(json.dumps(self.local_inventory))
        self.remote_inventory["self"]["hostId"] = "remote"
        for target in self.remote_inventory["targets"]:
            target["key"]["hostId"] = "remote"
        self.calls: list[list[str]] = []
        self.mode = "success"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def mesh(self, routes: tuple[str, ...] = ("peer",)) -> Mesh:
        remote = MeshHost(
            "remote",
            "Peer;$(touch shell-injection-proof)",
            False,
            ("remote-alias",),
            tuple(Route(route, index) for index, route in enumerate(routes)),
        )
        return Mesh(
            "sha256:" + "a" * 64,
            "ssh",
            5,
            2,
            (MeshHost("snap", "Snap", True, ("OS80H1VV3",), ()), remote),
        )

    def result(self, argv: list[str], returncode: int, stdout=b"", stderr=b""):
        return ProcessResult(tuple(argv), returncode, stdout, stderr)

    def runner(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == str(self.path):
            route = argv[-2]
            if self.mode == "fallback" and route == "first":
                return self.result(argv, 255, b"", b"connect failed")
            if self.mode == "identity" or self.mode == "missing":
                remote_inventory = dict(self.remote_inventory)
                remote_inventory["self"] = dict(remote_inventory["self"])
                if self.mode == "identity":
                    remote_inventory["self"]["hostId"] = "wrong-host"
                    remote_inventory["targets"] = [
                        dict(item) for item in remote_inventory["targets"]
                    ]
                    for item in remote_inventory["targets"]:
                        item["key"] = dict(item["key"])
                        item["key"]["hostId"] = "wrong-host"
                elif self.mode == "missing":
                    return self.shell(argv, kwargs, self.root / "missing helper")
                return self.shell(argv, kwargs, inventory=remote_inventory)
            return self.shell(argv, kwargs, inventory=self.remote_inventory)
        if argv[0] == "/local/host-helper":
            return self.result(argv, 0, json.dumps(self.local_inventory).encode() + b"\n")
        raise AssertionError(f"unexpected subprocess: {argv!r}")

    def shell(self, argv, kwargs, helper=None, inventory=None):
        # Execute the exact remote command string through a real /bin/sh parser.
        # This tests OpenSSH's remote-shell argument boundary, not just argv.
        remote_command = argv[-1]
        env = dict(kwargs.get("env", {}))
        if helper is not None:
            helper.parent.mkdir(parents=True, exist_ok=True)
            helper.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
            helper.chmod(0o755)
            remote_command = remote_command.replace("/unused/helper", str(helper))
        elif inventory is not None:
            source = json.dumps(inventory, ensure_ascii=False, separators=(",", ":"))
            # The selected helper token is an absolute path containing shell
            # syntax. It must survive shlex.join as one executable argument.
            helper_path = Path(self.helper_path)
            helper_path.parent.mkdir(parents=True, exist_ok=True)
            helper_path.write_text(
                "#!"
                + shutil.which("python3")
                + "\n"
                + "import json, pathlib, sys\n"
                + f"pathlib.Path({str(self.helper_log)!r}).write_text(json.dumps(sys.argv[1:]))\n"
                + f"print({source!r})\n",
                encoding="utf-8",
            )
            helper_path.chmod(0o755)
        try:
            completed = subprocess.run(
                ["/bin/sh", "-c", remote_command],
                env=env,
                cwd=self.root,
                capture_output=True,
                timeout=kwargs.get("timeout", 2),
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise AssertionError("test remote command exceeded its bounded deadline") from None
        return self.result(argv, completed.returncode, completed.stdout, completed.stderr)

    @property
    def helper_path(self) -> str:
        return str(self.root / "remote helper;$(touch shell-injection-proof)")

    @property
    def helper_log(self) -> Path:
        return self.root / "remote-helper-argv.json"

    def transport(self, **kwargs) -> Transport:
        return Transport(
            env={"PATH": f"{self.bin}:/usr/bin:/bin"},
            runner=self.runner,
            mesh=self.mesh(kwargs.pop("routes", ("peer",))),
            local_helper="/local/host-helper",
            remote_helpers={"remote": self.helper_path},
            config_paths=kwargs.pop("config_paths", {}),
            **kwargs,
        )

    def test_fleet_keeps_partial_and_complete_host_results(self):
        config = self.root / "config ;$(touch shell-injection-proof)"
        transport = self.transport(config_paths={"remote": config})
        snapshot = transport.read_fleet()
        self.assertEqual(snapshot.mesh_revision, "sha256:" + "a" * 64)
        self.assertEqual([item.host_id for item in snapshot.hosts], ["snap", "remote"])
        local, remote = snapshot.hosts
        self.assertEqual(local.status, "partial")
        self.assertEqual(remote.status, "partial")
        self.assertIsNotNone(local.inventory)
        self.assertIsNotNone(remote.inventory)
        self.assertEqual(remote.route, "peer")
        self.assertTrue(self.helper_log.exists())
        helper_argv = json.loads(self.helper_log.read_text())
        self.assertEqual(
            helper_argv,
            ["inventory", "--json", "--config", str(config)],
        )
        self.assertFalse((self.root / "shell-injection-proof").exists())
        ssh_call = next(call for call in self.calls if call[0] == str(self.path))
        self.assertEqual(ssh_call[1], "-T")
        self.assertIn("BatchMode=yes", ssh_call)
        self.assertIn("StrictHostKeyChecking=yes", ssh_call)

    def test_absent_provider_does_not_make_successful_inventory_partial(self):
        self.local_inventory["providerCoverage"][1].update(
            status="missing", reason="provider_executable_missing"
        )
        self.local_inventory["targets"] = [
            item for item in self.local_inventory["targets"] if item["key"]["provider"] == "codex"
        ]
        snapshot = self.transport().read_fleet()
        self.assertEqual(snapshot.hosts[0].status, "complete")
        self.assertEqual(snapshot.hosts[0].inventory.errors, ())
        self.assertEqual(snapshot.hosts[1].status, "partial")

    def test_read_only_inventory_falls_back_before_authenticated_marker(self):
        self.mode = "fallback"
        transport = self.transport(routes=("first", "second"))
        snapshot = transport.read_fleet()
        remote = snapshot.hosts[1]
        self.assertEqual(remote.status, "partial")
        self.assertEqual(remote.route, "second")
        self.assertEqual(len([call for call in self.calls if call[0] == str(self.path)]), 2)

    def test_ambiguous_ssh_probe_does_not_retry_another_route(self):
        calls = []

        def runner(argv, **_kwargs):
            argv = list(argv)
            calls.append(argv)
            if argv[0] == str(self.path):
                raise ProcessError("probe_timeout", "controlled timeout")
            return self.result(argv, 0, json.dumps(self.local_inventory).encode())

        transport = Transport(
            env={"PATH": f"{self.bin}:/usr/bin:/bin"},
            runner=runner,
            mesh=self.mesh(("first", "second")),
            local_helper="/local/host-helper",
        )
        snapshot = transport.read_fleet()
        remote = snapshot.hosts[1]
        self.assertEqual(remote.status, "error")
        self.assertEqual(remote.error.code, "probe_timeout")
        self.assertIsNone(remote.route)
        self.assertEqual(len([call for call in calls if call[0] == str(self.path)]), 1)

    def test_self_identity_mismatch_is_terminal_after_marker(self):
        self.mode = "identity"
        snapshot = self.transport(routes=("first", "second")).read_fleet()
        remote = snapshot.hosts[1]
        self.assertEqual(remote.status, "error")
        self.assertEqual(remote.error.code, "host_identity_mismatch")
        self.assertEqual(len([call for call in self.calls if call[0] == str(self.path)]), 1)

    def test_missing_helper_after_marker_does_not_retry_route(self):
        self.mode = "missing"
        snapshot = self.transport(routes=("first", "second")).read_fleet()
        remote = snapshot.hosts[1]
        self.assertEqual(remote.status, "error")
        self.assertEqual(remote.error.code, "helper_failed")
        self.assertEqual(len([call for call in self.calls if call[0] == str(self.path)]), 1)

    def test_explicit_entry_quotes_reference_and_config_and_uses_one_tty_route(self):
        config = self.root / "config ;$(touch shell-injection-proof)"
        transport = self.transport(config_paths={"remote": config}, routes=("peer;touch", "unused"))
        pin = transport.pin_entry("remote")
        self.assertEqual(pin.route, "peer;touch")
        self.assertEqual(pin.config, str(config))
        reference = json.loads((FIXTURES / "entry-reference.json").read_text())
        reference["expectedHostId"] = "remote"
        reference["targetKey"]["hostId"] = "remote"
        reference["targetKey"]["home"] = "/tmp/native;$(touch shell-injection-proof)"
        reference_json = json.dumps(reference, separators=(",", ":"))
        argv = transport.entry_argv(reference_json, pin)
        self.assertEqual(argv[1], "-tt")
        self.assertIn("BatchMode=no", argv)
        self.assertEqual(argv[-2], "peer;touch")
        self.assertEqual(len(argv), 12)
        remote_command = argv[-1]
        fake = Path(self.helper_path)
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_text(
            "#!"
            + shutil.which("python3")
            + "\n"
            + "import json, pathlib, sys\n"
            + f"pathlib.Path({str(self.helper_log)!r}).write_text(json.dumps(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        completed = subprocess.run(
            ["/bin/sh", "-c", remote_command], cwd=self.root, capture_output=True, check=False
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        values = json.loads(self.helper_log.read_text())
        self.assertEqual(values[0:2], ["enter", "--reference-json"])
        self.assertEqual(values[2], reference_json)
        self.assertEqual(values[3:], ["--config", str(config)])
        self.assertFalse((self.root / "shell-injection-proof").exists())

    def test_malformed_saved_transport_and_wrong_host_reference_are_rejected(self):
        invalid = {
            "schemaVersion": 1,
            "kind": "ssh",
            "hostId": "remote",
            "meshRevision": None,
            "route": "-oProxyCommand=bad",
            "sshExecutable": "/usr/bin/ssh",
            "connectTimeoutSeconds": 5,
            "connectionAttempts": 1,
            "helper": "rofi-agent-plus-host",
        }
        with self.assertRaises(TransportError):
            PinnedTransport.parse(invalid)
        transport = self.transport()
        pin = transport.pin_entry("remote")
        reference = (FIXTURES / "entry-reference.json").read_text().strip()
        with self.assertRaises(TransportError):
            transport.entry_argv(reference, pin)

    def test_host_mesh_failure_does_not_fall_back_to_os_hostname(self):
        def failed_mesh(argv, **kwargs):
            return self.result(list(argv), 1, b"", b"mesh unavailable")

        executable = self.bin / "rofi-ssh-plus"
        executable.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        executable.chmod(0o755)
        transport = Transport(
            env={"PATH": f"{self.bin}:/usr/bin:/bin"},
            runner=failed_mesh,
        )
        with self.assertRaises(TransportError) as raised:
            transport.read_fleet()
        self.assertEqual(raised.exception.code, "mesh_unavailable")

    def test_entry_uses_a_fresh_current_mesh_route(self):
        meshes = (self.mesh(("first",)), self.mesh(("second",)))

        def which(name, path=None):
            return "/usr/bin/ssh" if name == "ssh" else "/configured/rofi-ssh-plus"

        transport = Transport(env={"PATH": f"{self.bin}:/usr/bin:/bin"}, runner=self.runner)
        with (
            patch("rofi_agent_plus.transport.shutil.which", side_effect=which),
            patch("rofi_agent_plus.transport.resolve_self_mesh", side_effect=meshes) as resolve,
        ):
            first = transport.pin_entry("remote")
            second = transport.pin_entry("remote")
        self.assertEqual(first.route, "first")
        self.assertEqual(second.route, "second")
        self.assertEqual(resolve.call_count, 2)

    def test_installed_local_entry_uses_isolated_same_interpreter_module(self):
        mesh = Mesh(
            "sha256:" + "b" * 64,
            "ssh",
            5,
            1,
            (MeshHost("snap", "Snap", True, ("OS80H1VV3",), ()),),
        )
        transport = Transport(env={"PATH": ""}, runner=self.runner, mesh=mesh)
        with patch("rofi_agent_plus.transport._artifact_root", return_value=self.root):
            pin = transport.pin_entry("snap")
            reference = (FIXTURES / "entry-reference.json").read_text().strip()
            argv = transport.entry_argv(reference, pin)
        self.assertEqual(
            argv[:4],
            (sys.executable, "-I", "-m", "rofi_agent_plus.host"),
        )

    def test_venv_python_symlink_is_not_resolved_outside_candidate(self):
        candidate = self.root / "candidate" / "bin"
        candidate.mkdir(parents=True)
        python = candidate / "python"
        python.symlink_to(sys.executable)
        mesh = Mesh(None, "ssh", 2, 1, (MeshHost("snap", "Snap", True, (), ()),))
        transport = Transport(env={"PATH": ""}, mesh=mesh)
        with (
            patch("rofi_agent_plus.transport._artifact_root", return_value=self.root),
            patch("rofi_agent_plus.transport.sys.executable", str(python)),
        ):
            pin = transport.pin_entry("snap")
            argv = transport.entry_argv(
                (FIXTURES / "entry-reference.json").read_text().strip(), pin
            )
        self.assertEqual(argv[0], str(python))
        self.assertNotEqual(argv[0], str(python.resolve()))

    def test_missing_mesh_cli_uses_public_local_identity_fallback(self):
        expected = local_only_mesh().local.host_id
        inventory = json.loads(json.dumps(self.local_inventory))
        inventory["self"]["hostId"] = expected
        for target in inventory["targets"]:
            target["key"]["hostId"] = expected

        def runner(argv, **_kwargs):
            if argv[0] != "/local/host-helper":
                raise AssertionError(argv)
            return self.result(list(argv), 0, json.dumps(inventory).encode())

        transport = Transport(env={"PATH": ""}, runner=runner, local_helper="/local/host-helper")
        snapshot = transport.read_fleet()
        self.assertIsNone(snapshot.mesh_revision)
        self.assertEqual([item.host_id for item in snapshot.hosts], [expected])


if __name__ == "__main__":
    unittest.main()

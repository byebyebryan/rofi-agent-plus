"""Malformed wire and real subprocess resource limits remain finite."""

from __future__ import annotations

import copy
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from rofi_agent_plus.config import ConfigError, load_direct_config
from rofi_agent_plus.desktop import observe_process
from rofi_agent_plus.host_mesh import HostMeshError, parse_mesh_document
from rofi_agent_plus.model import RecordError, parse_inventory
from rofi_agent_plus.process import ProcessError, run_bounded
from rofi_agent_plus.wire import WireError, decode_document, validate_string_bounds


class DirectBoundsTests(unittest.TestCase):
    def test_dialog_refresh_cancellation_reaps_its_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            pidfile = root / "probe.pid"
            helper = binary / "codex"
            helper.write_text(
                f"#!{sys.executable}\nimport os,time\nfrom pathlib import Path\n"
                f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n"
            )
            helper.chmod(0o700)
            entry = Path(__file__).resolve().parents[1] / "bin/rofi-agent-plus"
            env = dict(
                os.environ,
                PATH=str(binary),
                XDG_CACHE_HOME=str(root / "cache"),
                XDG_CONFIG_HOME=str(root / "config"),
                XDG_STATE_HOME=str(root / "state"),
            )
            env.pop("NIRI_SOCKET", None)
            process = subprocess.Popen(
                [sys.executable, str(entry), "_direct-refresh"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 3
                while not pidfile.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(pidfile.exists())
                child = observe_process(int(pidfile.read_text()))
                self.assertIsNotNone(child)
                process.send_signal(signal.SIGINT)
                process.communicate(timeout=3)
                self.assertFalse(child.alive())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_unhashable_status_has_structured_error(self):
        path = Path(__file__).parent / "fixtures/direct-host/inventory.json"
        original = json.loads(path.read_text())
        for section in ("targets", "providerCoverage"):
            value = copy.deepcopy(original)
            item = value[section][0]
            (item["capability"] if section == "targets" else item)["status"] = []
            with self.assertRaises(RecordError):
                parse_inventory(value)

    def test_deep_and_cyclic_extensions_have_structured_error(self):
        with self.assertRaises(WireError):
            decode_document(b"[" * 1500 + b"0" + b"]" * 1500 + b"\n", limit=1 << 20)
        value = []
        value.append(value)
        with self.assertRaises(WireError):
            validate_string_bounds(value, limit=4096)
        with self.assertRaises(HostMeshError):
            parse_mesh_document(b"[" * 1500 + b"0" + b"]" * 1500 + b"\n")

    def test_nonregular_config_cannot_block(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fifo"
            os.mkfifo(path)
            with self.assertRaises(ConfigError):
                load_direct_config(path)

    def test_real_probe_caps_both_streams(self):
        for stream in (1, 2):
            with self.assertRaises(ProcessError) as raised:
                run_bounded(
                    [sys.executable, "-c", f"import os;os.write({stream},b'x'*65536)"],
                    timeout=2,
                    stdout_limit=1024,
                    stderr_limit=1024,
                )
            self.assertEqual(raised.exception.code, "probe_output_too_large")

    def test_timeout_reaps_group_after_leader_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "child"
            code = (
                "import os,time;from pathlib import Path;pid=os.fork();Path("
                + repr(str(path))
                + ").write_text(str(os.getpid())) if pid==0 else None;time.sleep(30) if pid==0 else None"
            )
            with self.assertRaises(ProcessError) as raised:
                run_bounded([sys.executable, "-c", code], timeout=0.3)
            self.assertEqual(raised.exception.code, "probe_timeout")
            pid = int(path.read_text())
            deadline = time.monotonic() + 1
            while observe_process(pid) is not None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNone(observe_process(pid))

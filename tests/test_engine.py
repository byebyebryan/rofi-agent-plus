"""The public engine surface stays pure and does not import desktop modules."""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

from rofi_agent_plus import VERSION, app, engine


class EngineBoundaryTests(unittest.TestCase):
    def test_package_and_engine_export_only_versioned_error_surface(self):
        self.assertEqual(VERSION, engine.VERSION)
        self.assertTrue(issubclass(engine.PickerError, RuntimeError))
        for name in (
            "ProviderAdapter",
            "HostTarget",
            "RuntimeInstance",
            "SshPolicy",
            "launch_attach",
            "focus_existing_window",
            "provider_active_snapshot",
        ):
            self.assertFalse(hasattr(engine, name), name)

    def test_direct_commands_reject_retired_actions_and_legacy_options(self):
        error = io.StringIO()
        with patch("sys.stderr", error):
            self.assertEqual(2, app.main(["active"]))
        self.assertIn("unsupported", error.getvalue())
        self.assertIn("list --json", error.getvalue())

        error = io.StringIO()
        with patch("sys.stderr", error):
            self.assertEqual(2, app.diagnostic_main(["list", "--limit", "12", "--json"]))
        self.assertIn("unrecognized arguments", error.getvalue())

    def test_importing_app_does_not_load_observer_or_legacy_picker_modules(self):
        blocked = {
            "rofi_agent_plus.batch",
            "rofi_agent_plus.observer_contract",
            "rofi_agent_plus.session_identity",
            "rofi_agent_plus.contract_lifecycle",
            "rofi_agent_plus.viewer_state",
        }
        self.assertTrue(blocked.isdisjoint(sys.modules))

    def test_script_callback_wraps_refreshable_work_in_probe_cancellation(self):
        with (
            patch.dict("os.environ", {"ROFI_RETV": "10"}, clear=True),
            patch.object(app, "initial_frame", return_value=None),
            patch.object(app, "passive_probe_signals", return_value=nullcontext()) as signals,
            patch.object(app, "run_rofi", return_value=0),
        ):
            self.assertEqual(0, app.main([]))
        signals.assert_called_once_with()

    def test_direct_refresh_helper_remains_explicit_and_cancellable(self):
        context = Mock()
        with (
            patch.object(app, "build_context", return_value=context),
            patch.object(app, "passive_probe_signals", return_value=nullcontext()) as signals,
        ):
            self.assertEqual(0, app.diagnostic_main(["_direct-refresh"]))
        signals.assert_called_once_with()
        context.cache.refresh.assert_called_once_with(context.transport)


if __name__ == "__main__":
    unittest.main()

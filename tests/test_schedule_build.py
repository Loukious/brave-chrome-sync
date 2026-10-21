import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from schedule_build import schedule, dispatch
from generate_workflow import generate


class SchedulingTests(unittest.TestCase):
    def test_active_or_queued_recovery_blocks_automatic_dispatch(self):
        for status in ["in_progress", "pending", "queued", "requested", "waiting", "unknown"]:
            with self.subTest(status=status), \
                 patch("schedule_build.api", return_value={"workflow_runs": [
                     {"id": 1, "status": "completed", "head_branch": "main"},
                     {"id": 2, "status": status, "head_branch": "fix/recovery"}]}), \
                 patch("schedule_build.dispatch") as start:
                self.assertFalse(schedule("owner/repo"))
                start.assert_not_called()

    def test_idle_build_history_dispatches_once(self):
        with patch("schedule_build.api", return_value={"workflow_runs": [
                {"id": 1, "status": "completed", "conclusion": "failure"},
                {"id": 2, "status": "completed", "conclusion": "success"}]}), \
             patch("schedule_build.dispatch") as start:
            self.assertTrue(schedule("owner/repo"))
            start.assert_called_once_with("owner/repo")

    def test_pagination_cannot_hide_an_active_run(self):
        first = [{"id": number, "status": "completed"} for number in range(100)]
        with patch("schedule_build.api", side_effect=[
                {"workflow_runs": first}, {"workflow_runs": [{"id": 101, "status": "in_progress"}]}]) as api, \
             patch("schedule_build.dispatch") as start:
            self.assertFalse(schedule("owner/repo"))
            self.assertTrue(api.call_args.args[0].endswith("page=2"))
            start.assert_not_called()

    def test_api_error_prevents_dispatch(self):
        with patch("schedule_build.api", side_effect=OSError("offline")), \
             patch("schedule_build.dispatch") as start:
            with self.assertRaises(OSError):
                schedule("owner/repo")
            start.assert_not_called()

    def test_dispatch_uses_main_without_retrying_uncertain_response(self):
        response = io.BytesIO()
        response.status = 204
        with patch.dict(os.environ, {"GH_TOKEN": "test-token"}), \
             patch("schedule_build.urllib.request.urlopen", return_value=response) as send:
            dispatch("owner/repo")
            request = send.call_args.args[0]
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.full_url, "https://api.github.com/repos/owner/repo/actions/workflows/build-release.yml/dispatches")
            self.assertEqual(json.loads(request.data), {"ref": "main"})
            send.assert_called_once()
        with patch.dict(os.environ, {"GH_TOKEN": "test-token"}), \
             patch("schedule_build.urllib.request.urlopen", side_effect=OSError("response lost")) as send:
            with self.assertRaises(OSError):
                dispatch("owner/repo")
            send.assert_called_once()

    def test_expensive_workflow_only_has_explicit_dispatch_trigger(self):
        # Parse the actual generated event block, so adding an automatic trigger
        # anywhere in it fails this regression test.
        events = generate().split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        triggers = [line.strip() for line in events.splitlines()
                    if line.startswith("  ") and not line.startswith("   ")]
        self.assertEqual(triggers, ["workflow_dispatch:"])


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from stage import compile_checked, compile_budget


class CompilePreflightTests(unittest.TestCase):
    command = ["pnpm", "run", "build", "Release", "--target=create_dist_zips", "--gn=is_debug:false"]

    def test_slow_transfer_reduces_compilation_to_preserve_checkpoint_time(self):
        start = datetime(2026, 10, 21, tzinfo=timezone.utc)
        deadline = (start + timedelta(minutes=350)).isoformat()
        # A 55-minute download, 35-minute verify/restore and 10-minute setup.
        now = start + timedelta(minutes=100)
        budget = compile_budget(210 * 60, deadline, now)
        self.assertEqual(budget, 160 * 60)
        self.assertEqual((datetime.fromisoformat(deadline) - now).total_seconds() - budget, 90 * 60)
        self.assertEqual(compile_budget(210 * 60, deadline, start), 210 * 60)

    def test_expired_job_budget_checkpoints_without_starting_any_build(self):
        now = datetime(2026, 10, 21, tzinfo=timezone.utc)
        for remaining in [90, 30, -1]:
            with self.subTest(remaining=remaining), tempfile.TemporaryDirectory() as folder, \
                 patch("stage.timed_run") as build:
                budget = compile_budget(210 * 60, (now + timedelta(minutes=remaining)).isoformat(), now)
                self.assertEqual(budget, 0)
                self.assertEqual(compile_checked(self.command, Path(folder), budget, "source"), 124)
                build.assert_not_called()
                self.assertFalse((Path(folder) / "settings-typecheck.json").exists())

    def test_no_ci_deadline_keeps_configured_budget_and_invalid_deadline_fails(self):
        self.assertEqual(compile_budget(210 * 60), 210 * 60)
        for deadline in ["invalid", "2026-10-21T00:00:00"]:
            with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                compile_budget(210 * 60, deadline)

    def test_settings_failure_prevents_full_browser_build_and_success_marker(self):
        for result in [1, 124]:
            with self.subTest(result=result), tempfile.TemporaryDirectory() as folder, \
                 patch("stage.timed_run", return_value=result) as build:
                root = Path(folder)
                self.assertEqual(compile_checked(self.command, root, 100, "source"), result)
                self.assertEqual(build.call_count, 1)
                self.assertIn("--target=chrome/browser/resources/settings:build_ts", build.call_args.args[0])
                self.assertFalse((root / "settings-typecheck.json").exists())

    def test_settings_success_is_checkpointed_and_both_phases_share_one_budget(self):
        with tempfile.TemporaryDirectory() as folder, patch("stage.timed_run", return_value=0) as build, \
             patch("stage.time.monotonic", side_effect=[10, 12, 40]):
            root = Path(folder)
            self.assertEqual(compile_checked(self.command, root, 100, "source"), 0)
            self.assertEqual(build.call_args_list[0].args[2], 98)
            self.assertEqual(build.call_args_list[1].args, (self.command, root / "src/brave", 70))
            self.assertEqual(json.loads((root / "settings-typecheck.json").read_text())["patch_digest"], "source")

    def test_resumed_matching_source_skips_preflight_but_changed_source_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / "settings-typecheck.json"
            marker.write_text(json.dumps({"patch_digest":"old", "target":"chrome/browser/resources/settings:build_ts"}))
            with patch("stage.timed_run", return_value=0) as build:
                self.assertEqual(compile_checked(self.command, root, 100, "old"), 0)
                self.assertEqual(build.call_count, 1)
                self.assertEqual(build.call_args.args[0], self.command)
            with patch("stage.timed_run", return_value=0) as build:
                self.assertEqual(compile_checked(self.command, root, 100, "new"), 0)
                self.assertEqual(build.call_count, 2)

    def test_expired_budget_after_preflight_preserves_completion_for_next_stage(self):
        with tempfile.TemporaryDirectory() as folder, patch("stage.timed_run", return_value=0) as build, \
             patch("stage.time.monotonic", side_effect=[0, 1, 101]):
            root = Path(folder)
            self.assertEqual(compile_checked(self.command, root, 100, "source"), 124)
            self.assertEqual(build.call_count, 1)
            self.assertTrue((root / "settings-typecheck.json").exists())


if __name__ == "__main__":
    unittest.main()

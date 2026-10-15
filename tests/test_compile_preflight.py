import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from stage import compile_checked


class CompilePreflightTests(unittest.TestCase):
    command = ["pnpm", "run", "build", "Release", "--target=create_dist_zips", "--gn=is_debug:false"]

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

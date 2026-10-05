import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from resume_checkpoint import inputs, check_marker, check_run


class ResumeTests(unittest.TestCase):
    def fixture(self):
        config = {"gn_args": {"is_debug": False}, "release_channel": "release",
                  "runner": "windows", "node_version": "24.x", "pnpm_version": "11.11.0",
                  "patch_revision": 6, "compile_minutes": 210}
        return {"config.json": json.dumps(config).encode(), ".gitattributes": b"* text eol=lf\n",
                "patches/series": b"one.patch\n", "patches/one.patch": b"source patch\n",
                "scripts/prepare.py": b"prepare", "scripts/runner-setup.ps1": b"toolchain"}

    def test_source_and_build_settings_changes_prevent_reuse(self):
        original = self.fixture()
        expected = inputs(original.__getitem__)[0]
        for field in ["patches/one.patch", "scripts/prepare.py", "scripts/runner-setup.ps1"]:
            changed = {**original, field: original[field] + b"change"}
            self.assertNotEqual(inputs(changed.__getitem__)[0], expected)
        for field, value in [("gn_args", {"is_debug": True}), ("release_channel", "nightly"),
                             ("node_version", "26.x"), ("runner", "other")]:
            changed = copy.deepcopy(original)
            config = json.loads(changed["config.json"])
            config[field] = value
            changed["config.json"] = json.dumps(config).encode()
            self.assertNotEqual(inputs(changed.__getitem__)[0], expected)

    def test_helper_revision_and_time_budget_do_not_invalidate_browser_objects(self):
        data = self.fixture()
        expected = inputs(data.__getitem__)[0]
        config = json.loads(data["config.json"])
        config.update(patch_revision=7, compile_minutes=180)
        data["config.json"] = json.dumps(config).encode()
        self.assertEqual(inputs(data.__getitem__)[0], expected)

    def test_marker_migration_requires_matching_source_and_original_pipeline_digest(self):
        marker = {"upstream_tag": "v1.99.9", "upstream_sha": "source", "patch_digest": "old"}
        with patch("resume_checkpoint.check_compatibility", return_value="old"):
            updated = check_marker(marker, "v1.99.9", "source", "new", "commit")
            self.assertEqual(updated["patch_digest"], "new")
            for tag, sha, identity in [("v1.99.8", "source", marker),
                                      ("v1.99.9", "wrong", marker),
                                      ("v1.99.9", "source", {**marker, "patch_digest": "wrong"})]:
                with self.assertRaisesRegex(ValueError, "does not match"):
                    check_marker(identity, tag, sha, "new", "commit")
        with self.assertRaisesRegex(ValueError, "does not match"):
            check_marker(marker, "v1.99.9", "source", "new")

    def test_fork_and_pull_request_runs_are_rejected_before_fetch(self):
        run = {"head_repository": {"full_name": "owner/repo"}, "event": "push",
               "path": ".github/workflows/build-release.yml", "head_sha": "a" * 40}
        for changed in [{**run, "event": "pull_request"},
                        {**run, "head_repository": {"full_name": "fork/repo"}},
                        {**run, "path": ".github/workflows/checks.yml"}]:
            with patch("resume_checkpoint.api", return_value=changed), \
                 patch("resume_checkpoint.subprocess.run") as git:
                with self.assertRaisesRegex(ValueError, "trusted build workflow"):
                    check_run("123", "owner/repo")
                git.assert_not_called()


if __name__ == "__main__":
    unittest.main()

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from resume_checkpoint import inputs, check_marker, check_run, original_digest, appended_patches, apply_checkpoint_patches


class ResumeTests(unittest.TestCase):
    def cpp_patch(self, name="browser/net/fix.cc"):
        return f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n@@ -1 +1 @@\n-old\n+new\n".encode()

    def test_appended_cpp_fix_is_replayed(self):
        old = self.fixture()
        current = {**old, "patches/series": b"one.patch\nfix.patch\n",
                   "patches/fix.patch": self.cpp_patch()}
        self.assertEqual(appended_patches(old.__getitem__, current.__getitem__), ["fix.patch"])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, data in current.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            with patch("resume_checkpoint.git_file", side_effect=lambda commit, path: old[path]), \
                 patch("resume_checkpoint.ROOT", root), patch("resume_checkpoint.subprocess.run") as git:
                apply_checkpoint_patches(Path("build"), "commit")
                self.assertEqual(git.call_count, 1)
                self.assertEqual(git.call_args.kwargs["cwd"], Path("build/src/brave"))
                self.assertEqual(git.call_args.args[0], ["git", "am", "--keep-cr", str(root / "patches/fix.patch")])
                self.assertTrue(git.call_args.kwargs["check"])

    def test_appended_fix_cannot_hide_changed_original_inputs(self):
        old = self.fixture()
        current = {**old, "patches/series": b"one.patch\nfix.patch\n", "patches/fix.patch": self.cpp_patch()}
        for field in ["patches/one.patch", "scripts/prepare.py", "scripts/runner-setup.ps1"]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "changed"):
                appended_patches(old.__getitem__, {**current, field: old[field] + b"changed"}.__getitem__)
        changed = {**current, "config.json": current["config.json"].replace(b'"windows"', b'"other"')}
        with self.assertRaisesRegex(ValueError, "changed"):
            appended_patches(old.__getitem__, changed.__getitem__)
        for series in [b"fix.patch\none.patch\n", b"fix.patch\n"]:
            with self.assertRaisesRegex(ValueError, "changed"):
                appended_patches(old.__getitem__, {**current, "patches/series": series}.__getitem__)

    def test_append_requires_normal_brave_cpp_without_initialization_changes(self):
        old = self.fixture()
        for name in ["patches/chromium.patch", "browser/BUILD.gn", "rewrite/fix.cc", "browser/../fix.cc"]:
            current = {**old, "patches/series": b"one.patch\nfix.patch\n", "patches/fix.patch": self.cpp_patch(name)}
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Brave C\\+\\+"):
                appended_patches(old.__getitem__, current.__getitem__)

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
        with patch("resume_checkpoint.check_compatibility", return_value={"old", "windows-old"}):
            updated = check_marker(marker, "v1.99.9", "source", "new", "commit")
            self.assertEqual(updated["patch_digest"], "new")
            self.assertEqual(check_marker({**marker, "patch_digest": "windows-old"},
                                          "v1.99.9", "source", "new", "commit"), updated)
            for tag, sha, identity in [("v1.99.8", "source", marker),
                                      ("v1.99.9", "wrong", marker),
                                      ("v1.99.9", "source", {**marker, "patch_digest": "wrong"})]:
                with self.assertRaisesRegex(ValueError, "does not match"):
                    check_marker(identity, tag, sha, "new", "commit")
        with self.assertRaisesRegex(ValueError, "does not match"):
            check_marker(marker, "v1.99.9", "source", "new")

    def test_original_pipeline_hash_reproduces_both_platform_orders(self):
        data = self.fixture()
        data.update({"scripts/Start.ps1": b"start", "scripts/Update.ps1": b"update"})
        groups = ["scripts/prepare.py", "scripts/Start.ps1", "scripts/Update.ps1", "scripts/runner-setup.ps1"]
        base = [".gitattributes", "config.json", "patches/series", "patches/one.patch"]
        result = unittest.mock.Mock(stdout="\n".join(data))
        with patch("resume_checkpoint.subprocess.run", return_value=result), \
             patch("resume_checkpoint.git_file", side_effect=lambda commit, path: data[path]):
            canonical = original_digest("commit", ["one.patch"])
            legacy = original_digest("commit", ["one.patch"], windows_order=True)
        def expected(paths):
            identity = hashlib.sha256()
            for path in paths:
                identity.update(path.encode() + b"\0" + data[path] + b"\0")
            return identity.hexdigest()
        self.assertEqual(canonical, expected(base + groups))
        self.assertEqual(legacy, expected(base + [groups[0], groups[3], groups[1], groups[2]]))
        self.assertNotEqual(canonical, legacy)

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

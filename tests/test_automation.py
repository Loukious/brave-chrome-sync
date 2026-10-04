import hashlib
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from upstream import select_release, version, main as resolve_main
from verify_release import verify


class ReleaseSelectionTests(unittest.TestCase):
    def test_nightly_source_can_produce_our_release_distribution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = {"release_channel": "release", "patch_revision": 2,
                      "runner": "windows", "node_version": "24.x", "pnpm_version": "11.11.0"}
            (root / "config.json").write_text(json.dumps(config))
            result = io.StringIO()
            source = {"tag_name": "v1.99.9", "name": "Nightly v1.99.9", "prerelease": True, "draft": False}
            with patch("upstream.ROOT", root), patch("upstream.api", return_value=source), \
                 patch("upstream.digest", return_value="0123456789abcdef"), \
                 patch("upstream.resolve_commit", return_value="abc"), \
                 patch.object(sys, "argv", ["upstream.py", "--tag", "v1.99.9", "--repository", ""]), \
                 patch.dict(os.environ, {"GITHUB_OUTPUT": ""}), contextlib.redirect_stdout(result):
                resolve_main()
            output = json.loads(result.getvalue())
            self.assertEqual(output["upstream_tag"], "v1.99.9")
            self.assertEqual(output["channel"], "release")
            self.assertEqual(output["prerelease"], "false")

    def test_nightly_is_not_selected_as_stable_despite_upstream_flag(self):
        releases = [
            {"name": "Nightly v1.99.9", "tag_name": "v1.99.9", "prerelease": False},
            {"name": "Release v1.96.61", "tag_name": "v1.96.61", "prerelease": False},
            {"name": "Beta v1.98.48", "tag_name": "v1.98.48", "prerelease": True},
        ]
        self.assertEqual(select_release(releases, "release", "1.0.0")["tag_name"], "v1.96.61")
        self.assertEqual(select_release(releases, "nightly", "1.99.8")["tag_name"], "v1.99.9")

    def test_numeric_order_and_drafts(self):
        releases = [{"name": "Nightly " + tag, "tag_name": tag} for tag in ["v1.99.9", "v1.99.10"]]
        releases.append({"name": "Nightly v1.100.1", "tag_name": "v1.100.1", "draft": True})
        self.assertEqual(select_release(releases, "nightly", "1.99.8")["tag_name"], "v1.99.10")

    def test_reject_older_or_unsafe_version(self):
        with self.assertRaises(ValueError):
            select_release([{"name": "Release v1.96.61", "tag_name": "v1.96.61"}], "release", "1.99.8")
        for tag in ["main", "v1.2.3\ncontents", "v1.2.3;echo bad", "../../other"]:
            with self.assertRaises(ValueError):
                version(tag)


class PublishTests(unittest.TestCase):
    def make_payload(self, root):
        metadata = {"release_tag": "v1.99.8-sync.r1.0123456789ab", "upstream_sha": "abc",
                    "patch_digest": "digest", "upstream_tag": "v1.99.8", "chromium_version": "155.0.0.0",
                    "pipeline_commit": "def", "browser_smoke_test": "Chrome/155.0.0.0",
                    "devtools_target_smoke_test": True,
                    "shared_default_profile_smoke_test": True,
                    "taskbar_launch_smoke_test": True,
                    "default_browser_registration_test": True,
                    "gn_args": {"is_official_build": True, "is_debug": False, "is_component_build": False},
                    "native_updater": True, "updater_protocol": 1, "channel": "nightly",
                    "installer": "fixture-windows-x64-setup.exe", "installer_smoke_test": "Chrome/155.0.0.0"}
        (root / "build-metadata.json").write_text(json.dumps(metadata))
        for name in ["patched-brave-core.tar.gz", "LICENSE", "Update-BraveChromeSync.ps1", "Start-BraveChromeSync.ps1"]:
            (root / name).write_text("fixture")
        with zipfile.ZipFile(root / "browser.zip", "w") as stream:
            stream.writestr("brave.exe", "fixture")
            stream.writestr("chrome.dll", "fixture")
            stream.writestr("SyncUpdater.exe", "fixture")
            stream.writestr("sync-release.json", json.dumps({"release_tag": metadata["release_tag"], "channel": "nightly", "protocol": 1}))
        (root / metadata["installer"]).write_bytes(b"MZfixture")
        self.write_sums(root)

    def write_sums(self, root):
        lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
                 for p in sorted(root.iterdir()) if p.is_file() and p.name != "SHA256SUMS"]
        (root / "SHA256SUMS").write_text("".join(lines))

    def test_valid_payload_and_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.make_payload(root)
            with patch.dict(os.environ, {"RELEASE_TAG": "v1.99.8-sync.r1.0123456789ab",
                                         "UPSTREAM_SHA": "abc", "PATCH_DIGEST": "digest"}):
                verify(root)
                (root / "browser.zip").write_bytes(b"tampered")
                with self.assertRaises(ValueError):
                    verify(root)

    def test_missing_browser_regression_checks_never_publish(self):
        for field in ["devtools_target_smoke_test", "shared_default_profile_smoke_test", "taskbar_launch_smoke_test", "default_browser_registration_test"]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                self.make_payload(root)
                metadata = json.loads((root / "build-metadata.json").read_text())
                del metadata[field]
                (root / "build-metadata.json").write_text(json.dumps(metadata))
                self.write_sums(root)
                with self.assertRaises(ValueError):
                    verify(root)

    def test_wrong_upstream_never_publishes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.make_payload(root)
            with patch.dict(os.environ, {"RELEASE_TAG": "wrong"}):
                with self.assertRaises(ValueError):
                    verify(root)

    def test_development_or_component_build_never_publishes(self):
        for field, value in (("is_official_build", False), ("is_debug", True), ("is_component_build", True)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                self.make_payload(root)
                metadata = json.loads((root / "build-metadata.json").read_text())
                metadata["gn_args"][field] = value
                (root / "build-metadata.json").write_text(json.dumps(metadata))
                with self.assertRaisesRegex(ValueError, "Release configuration"):
                    verify(root)

    def test_versioned_library_without_normalization_never_publishes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.make_payload(root)
            with zipfile.ZipFile(root / "browser.zip") as original:
                files = {name: original.read(name) for name in original.namelist()}
            files["155.1.99.8/chrome.dll"] = files.pop("chrome.dll")
            with zipfile.ZipFile(root / "browser.zip", "w") as stream:
                for name, data in files.items():
                    stream.writestr(name, data)
            self.write_sums(root)
            with patch.dict(os.environ, {"RELEASE_TAG": "v1.99.8-sync.r1.0123456789ab",
                                         "UPSTREAM_SHA": "abc", "PATCH_DIGEST": "digest"}):
                with self.assertRaisesRegex(ValueError, "chrome.dll beside"):
                    verify(root)


if __name__ == "__main__":
    unittest.main()

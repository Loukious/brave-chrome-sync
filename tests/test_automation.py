import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from upstream import select_release, version
from verify_release import verify


class ReleaseSelectionTests(unittest.TestCase):
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
                    "native_updater": True, "updater_protocol": 1, "channel": "nightly",
                    "installer": "fixture-windows-x64-setup.exe", "installer_smoke_test": "Chrome/155.0.0.0"}
        (root / "build-metadata.json").write_text(json.dumps(metadata))
        for name in ["patched-brave-core.tar.gz", "LICENSE", "Update-BraveChromeSync.ps1", "Start-BraveChromeSync.ps1"]:
            (root / name).write_text("fixture")
        with zipfile.ZipFile(root / "browser.zip", "w") as stream:
            stream.writestr("brave.exe", "fixture")
            stream.writestr("155/chrome.dll", "fixture")
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

    def test_wrong_upstream_never_publishes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.make_payload(root)
            with patch.dict(os.environ, {"RELEASE_TAG": "wrong"}):
                with self.assertRaises(ValueError):
                    verify(root)


if __name__ == "__main__":
    unittest.main()

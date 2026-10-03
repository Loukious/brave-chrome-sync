"""Verify the real Brave archive layout remains launchable and updateable."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from package_windows import normalize_payload


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.app = Path(self.temporary.name) / "Chrome-bin"
        self.app.mkdir()
        (self.app / "brave.exe").write_bytes(b"browser")

    def versioned(self):
        version = self.app / "155.1.99.0"
        version.mkdir()
        (version / "chrome.dll").write_bytes(b"library")
        return version

    def test_brave_archive_places_runtime_and_resources_beside_executable(self):
        version = self.versioned()
        (version / "base.dll").write_bytes(b"component")
        (version / "locales").mkdir()
        (version / "locales/en-US.pak").write_bytes(b"locale")
        (version / "resources.pak").write_bytes(b"resources")
        browser, payload = normalize_payload(Path(self.temporary.name))
        self.assertEqual(browser, self.app / "brave.exe")
        self.assertEqual(payload, self.app)
        self.assertEqual((payload / "chrome.dll").read_bytes(), b"library")
        self.assertEqual((payload / "base.dll").read_bytes(), b"component")
        self.assertEqual((payload / "locales/en-US.pak").read_bytes(), b"locale")
        self.assertEqual((payload / "resources.pak").read_bytes(), b"resources")

    def test_flat_layout_keeps_browser_files(self):
        (self.app / "chrome.dll").write_bytes(b"library")
        self.assertEqual(normalize_payload(self.app), (self.app / "brave.exe", self.app))

    def test_collision_fails_before_moving_any_runtime_files(self):
        version = self.versioned()
        (self.app / "resources.pak").write_bytes(b"root")
        (version / "resources.pak").write_bytes(b"version")
        with self.assertRaisesRegex(ValueError, "collide"):
            normalize_payload(self.app)
        self.assertTrue((version / "chrome.dll").exists())
        self.assertEqual((self.app / "resources.pak").read_bytes(), b"root")

    def test_unknown_library_layout_is_rejected(self):
        (self.app / "unknown").mkdir()
        (self.app / "unknown/chrome.dll").write_bytes(b"library")
        with self.assertRaisesRegex(ValueError, "Unexpected"):
            normalize_payload(self.app)

    def test_multiple_libraries_are_rejected(self):
        self.versioned()
        (self.app / "chrome.dll").write_bytes(b"duplicate")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            normalize_payload(self.app)

    def test_missing_library_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            normalize_payload(self.app)


if __name__ == "__main__":
    unittest.main()

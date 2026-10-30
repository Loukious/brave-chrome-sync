"""Keep the Google account hook independent of neighboring Brave UI changes."""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FILE = "browser/ui/views/profiles/brave_profile_menu_view.cc"
HOOK = """// We don't want feature buttons to manage google account
void BraveProfileMenuView::BuildFeatureButtons() {
  MaybeBuildCloseBrowsersButton();
}
"""


class ProfileMenuPatchTests(unittest.TestCase):
    def apply(self, source):
        patch = (ROOT / "patches/0001-Restore-Chromium-Google-sign-in-and-Sync-paths.patch").read_text()
        header = f"diff --git a/{FILE} b/{FILE}\n"
        section = header + patch.split(header, 1)[1].split("diff --git ", 1)[0]
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / FILE
            target.parent.mkdir(parents=True)
            target.write_text(source, encoding="utf-8", newline="\n")
            result = subprocess.run(["git", "apply", "-"], input=section.encode(),
                                    cwd=folder, capture_output=True)
            return result, target.read_text()

    def test_neighboring_icon_and_function_changes_leave_account_hook_intact(self):
        for neighbor in [
            "void BraveProfileMenuView::MaybeBuildCloseBrowsersButton() {\n"
            "  AddFeatureButton(title, callback, vector_icons::kCloseOldIcon);\n}\n\n",
            "void BraveProfileMenuView::MaybeBuildCloseBrowsersButton() {\n"
            "  AddFeatureButton(title, callback, kLeoCloseIcon);\n}\n\n",
            "// Future neighboring UI implementation.\n"
            "void BraveProfileMenuView::MaybeBuildCloseBrowsersButton() {\n"
            "  BuildDifferentCloseButton();\n}\n\n",
        ]:
            with self.subTest(neighbor=neighbor):
                result, actual = self.apply(neighbor + HOOK)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                expected = neighbor + "void BraveProfileMenuView::BuildFeatureButtons() {\n  ProfileMenuView::BuildFeatureButtons();\n}\n"
                self.assertEqual(actual, expected)

    def test_changed_account_hook_requires_review(self):
        source = HOOK.replace("MaybeBuildCloseBrowsersButton();", "BuildNewAccountButtons();")
        result, actual = self.apply(source)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(actual, source)

    def test_missing_account_hook_is_not_silently_accepted(self):
        source = "void BraveProfileMenuView::BuildOtherButtons() {}\n"
        result, actual = self.apply(source)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(actual, source)


if __name__ == "__main__":
    unittest.main()

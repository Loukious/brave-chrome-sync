from pathlib import Path
import re
import subprocess
import tempfile
import unittest


class SyncStartupPatchTests(unittest.TestCase):
    def test_startup_guard_applies_before_and_after_upstream_added_braces(self):
        root = Path(__file__).resolve().parents[1]
        patch = (root / "patches/0001-Restore-Chromium-Google-sign-in-and-Sync-paths.patch").read_text()
        section = patch.split("diff --git a/browser/brave_browser_main_parts.cc", 1)[1].split("diff --git", 1)[0]
        hunk = next(h for h in re.split(r"(?m)(?=^@@ )", section) if "-#if !BUILDFLAG(IS_ANDROID)" in h)
        file = "browser/brave_browser_main_parts.cc"
        for braces in [False, True]:
            condition = '    if (!command_line->HasSwitch(syncer::kDisableSync))'
            body = '      command_line->AppendSwitch(syncer::kDisableSync);'
            if braces:
                condition += ' {'
                body += '\n    }'
            source = '\nvoid ChromeBrowserMainParts::PreProfileInit() {\n  ChromeBrowserMainParts_ChromiumImpl::PreProfileInit();\n#if !BUILDFLAG(IS_ANDROID)\n  auto* command_line = base::CommandLine::ForCurrentProcess();\n  if (!base::FeatureList::IsEnabled(brave_sync::features::kBraveSync)) {\n    // Disable sync temporarily\n' + condition + '\n' + body + '\n  }\n#endif\n}\n'
            with self.subTest(braces=braces), tempfile.TemporaryDirectory() as folder:
                checkout = Path(folder)
                target = checkout / file
                target.parent.mkdir(parents=True)
                target.write_text(source)
                data = f'diff --git a/{file} b/{file}\n--- a/{file}\n+++ b/{file}\n{hunk}'
                subprocess.run(['git', 'apply', '-'], input=data.encode(), cwd=checkout,
                               check=True, capture_output=True)
                restored = target.read_text()
                self.assertIn('ChromiumImpl::PreProfileInit();\n#if 0\n', restored)
                self.assertNotIn('#if !BUILDFLAG(IS_ANDROID)', restored)


if __name__ == '__main__':
    unittest.main()

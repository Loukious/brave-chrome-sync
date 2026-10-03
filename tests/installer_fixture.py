"""Compile the production installer around a tiny fixture executable."""
import sys
from pathlib import Path
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from package_windows import package_browser
from upstream import ROOT

archive = ROOT / "work/installer-fixture.zip"
fixture = ROOT / "work/fixture"
(fixture / "chrome.dll").write_text("fixture")
with zipfile.ZipFile(archive, "w") as stream:
    for path in fixture.iterdir():
        if path.is_file():
            # Match Brave's actual Chrome-bin archive, rather than assuming a
            # flat payload that hides packaging problems from installer tests.
            name = path.name if path.name == "brave.exe" else "155.1.99.8/" + path.name
            stream.write(path, name)
dist = ROOT / "work/installer-fixture-dist"
dist.mkdir(exist_ok=True)
package_browser(archive, dist, "v1.99.8-sync.r2.0123456789ab", "nightly", "v1.99.8", "2026-10-03T00:00:00Z")

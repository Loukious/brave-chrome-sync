"""Validate the nested Chromium shell patch against the selected source tag."""
import argparse
import json
from pathlib import Path
import subprocess
import uuid

from chromium_source import read_chromium_source

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("brave", type=Path)
args = parser.parse_args()
version = json.loads((args.brave / "package.json").read_text())["config"]["projects"]["chrome"]["tag"]
fixture = ROOT / "work" / ("native-shell-patch-" + uuid.uuid4().hex)
source = fixture / "chrome/installer/util/shell_util.cc"
source.parent.mkdir(parents=True)
source.write_bytes(read_chromium_source(version, "chrome/installer/util/shell_util.cc"))
subprocess.run(["git", "init", "--quiet", str(fixture)], check=True)
patch = (args.brave / "patches/chrome-installer-util-shell_util.cc.patch").read_bytes()
subprocess.run(["git", "apply", "--check", "-"], input=patch, check=True, cwd=fixture)
subprocess.run(["git", "apply", "-"], input=patch, check=True, cwd=fixture)
content = source.read_text()
for hook in ["BRAVE_PROBE_CURRENT_DEFAULT_HANDLERS", "BRAVE_REGISTER_CHROME_BROWSER_IMPL",
             "BRAVE_SHOW_MAKE_CHROME_DEFAULT_SYSTEM_UI", "BRAVE_REGISTER_CHROME_FOR_PROTOCOLS"]:
    if content.count(hook) != 1:
        raise ValueError(f"Missing or duplicated native registration hook: {hook}")
print(f"Native Windows shell patch applies to Chromium {version}")

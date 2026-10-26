"""Compile the native registration code shipped in the maintained Brave patch."""
import os
import json
from pathlib import Path
import subprocess
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
work = ROOT / "work/native-registration"
work.mkdir(parents=True, exist_ok=True)
name = "chromium_src/chrome/installer/util/sync_browser_registration.h"
fixture = work / ("source-" + uuid.uuid4().hex)
subprocess.run(["git", "init", "--quiet", str(fixture)], check=True)
for filename in (ROOT / "patches/series").read_text().splitlines():
    if not filename or filename.startswith("#"):
        continue
    patch = ROOT / "patches" / filename
    if f"diff --git a/{name} b/{name}" in patch.read_text():
        subprocess.run(["git", "apply", "--include=" + name, str(patch)], cwd=fixture, check=True)
# Use the actual Chromium wrapper, including its macro hygiene checks, rather
# than a test shim that could hide the production include-order failure.
config = json.loads((ROOT / "config.json").read_text())
url = f"https://raw.githubusercontent.com/brave/brave-core/{config['patch_base_commit']}/package.json"
with urllib.request.urlopen(url, timeout=60) as response:
    version = json.load(response)["config"]["projects"]["chrome"]["tag"]
for relative in ["base/win/shlwapi.h", "base/win/windows_defines.inc", "base/win/windows_undefines.inc"]:
    target = fixture / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f"https://raw.githubusercontent.com/chromium/chromium/{version}/{relative}", timeout=60) as response:
        target.write_bytes(response.read())
devcmd = Path("C:/BuildTools2026/Common7/Tools/VsDevCmd.bat")
if not devcmd.exists():
    vswhere = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
    installation = subprocess.run([vswhere, "-latest", "-products", "*", "-requires",
                                   "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                                  capture_output=True, check=True, text=True).stdout.strip()
    devcmd = Path(installation) / "Common7/Tools/VsDevCmd.bat"
if not devcmd.is_file():
    raise ValueError("Visual C++ development tools are missing")
command = work / "compile.cmd"
command.write_text(f'@echo off\ncall "{devcmd}" -no_logo -arch=x64 -host_arch=x64\n'
                   'if errorlevel 1 exit /b 1\n'
                   f'cl /nologo /std:c++20 /EHsc /W4 /WX /DUNICODE /D_UNICODE /I"{fixture}" '
                   f'/I"{fixture / Path(name).parent}" "{ROOT / "tests/native_registration.cc"}" '
                   f'/Fo"{work / "native_registration.obj"}" /Fe"{work / "native_registration.exe"}" '
                   '/link advapi32.lib shlwapi.lib\n', encoding="utf-8")
subprocess.run(["cmd.exe", "/d", "/c", str(command)], check=True, cwd=ROOT)

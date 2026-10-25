"""Compile the native registration code shipped in the maintained Brave patch."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
work = ROOT / "work/native-registration"
work.mkdir(parents=True, exist_ok=True)
patch = (ROOT / "patches/0013-Use-stable-Windows-browser-registration.patch").read_text()
name = "chromium_src/chrome/installer/util/sync_browser_registration.h"
section = patch.split(f"diff --git a/{name} b/{name}\n", 1)[1].split("\ndiff --git ", 1)[0]
source = "\n".join(line[1:] for line in section.splitlines() if line.startswith("+") and not line.startswith("+++")) + "\n"
(work / "sync_browser_registration.h").write_text(source, encoding="utf-8")
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
                   f'cl /nologo /std:c++20 /EHsc /W4 /WX /I"{work}" "{ROOT / "tests/native_registration.cc"}" '
                   f'/Fo"{work / "native_registration.obj"}" /Fe"{work / "native_registration.exe"}" '
                   '/link advapi32.lib shlwapi.lib\n', encoding="utf-8")
subprocess.run(["cmd.exe", "/d", "/c", str(command)], check=True, cwd=ROOT)

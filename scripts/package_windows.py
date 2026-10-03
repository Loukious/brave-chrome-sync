"""Bundle the self-contained updater and build a per-user Windows installer."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile

from prepare import run
from upstream import ROOT

INNO_URL = "https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe"
INNO_SHA256 = "0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f"


def compiler():
    tools = ROOT / "work/tools"
    tools.mkdir(parents=True, exist_ok=True)
    setup = tools / "innosetup-7.1.0-x64.exe"
    if not setup.exists():
        urllib.request.urlretrieve(INNO_URL, setup)
    with setup.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != INNO_SHA256:
            raise ValueError("Inno Setup compiler download failed checksum validation")
    install = tools / "inno-7.1.0"
    executable = install / "ISCC.exe"
    if not executable.exists():
        # Use this workspace only; do not replace another installed compiler.
        run(setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER",
            "/NOICONS", f"/DIR={install}")
    if not executable.exists():
        raise ValueError("Inno Setup compiler is missing")
    return executable


def package_browser(archive, dist, release_tag, channel, version, published):
    work = ROOT / "work" / f"package-{release_tag}"
    if work.exists():
        raise ValueError(f"Packaging directory already exists: {work}")
    app = work / "app"
    with zipfile.ZipFile(archive) as stream:
        stream.extractall(app)
    browsers = list(app.rglob("brave.exe")) + list(app.rglob("chrome.exe"))
    if len(browsers) != 1:
        raise ValueError("Browser executable is missing or ambiguous")
    payload = browsers[0].parent
    publish = work / "updater"
    run("dotnet", "publish", ROOT / "updater/SyncBrowser.csproj", "-c", "Release",
        "-r", "win-x64", "--self-contained", "true", "-o", publish)
    shutil.copy2(publish / "SyncBrowser.exe", payload / "SyncUpdater.exe")
    (payload / "sync-release.json").write_text(json.dumps({"release_tag": release_tag,
        "channel": channel, "protocol": 1}, indent=2) + "\n", encoding="utf-8")
    # Package from the actual executable directory to keep installer/ZIP layouts identical.
    destination = dist / f"brave-chrome-sync-{release_tag}-windows-x64.zip"
    temporary = work / "payload.zip"
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as stream:
        for path in sorted(payload.rglob("*")):
            if path.is_file():
                stream.write(path, path.relative_to(payload).as_posix())
    shutil.copy2(temporary, destination)
    run(compiler(), f"/DPayloadDir={payload}", f"/DOutputDir={dist}",
        f"/DReleaseTag={release_tag}", f"/DChannel={channel}",
        f"/DAppVersion={version.lstrip('v')}", f"/DBrowserExe={browsers[0].name}",
        f"/DPublishedAt={published}", f"/DLicenseFile={ROOT / 'LICENSE'}",
        ROOT / "installer/browser.iss")
    setup = dist / f"brave-chrome-sync-{release_tag}-windows-x64-setup.exe"
    if setup.read_bytes()[:2] != b"MZ":
        raise ValueError("Installer is not a Windows executable")
    return destination, setup


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--channel", default="nightly")
    parser.add_argument("--version", required=True)
    parser.add_argument("--published", default="1970-01-01T00:00:00Z")
    args = parser.parse_args()
    args.dist.mkdir(parents=True, exist_ok=True)
    package_browser(args.archive.resolve(), args.dist.resolve(), args.release_tag,
                    args.channel, args.version, args.published)

"""Check provenance, checksums and archive contents before publishing."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import zipfile


def verify(root):
    metadata = json.loads((root / "build-metadata.json").read_text())
    if not metadata.get("browser_smoke_test"):
        raise ValueError("Packaged browser was not smoke tested")
    if metadata.get("native_updater") is not True or metadata.get("updater_protocol") != 1 or not metadata.get("installer_smoke_test"):
        raise ValueError("Native updater and installed browser were not validated")
    installer = metadata.get("installer", "")
    if Path(installer).name != installer or not installer.endswith("-windows-x64-setup.exe"):
        raise ValueError("Invalid installer filename")
    if (root / installer).read_bytes()[:2] != b"MZ":
        raise ValueError("Windows installer is missing or invalid")
    for environment, field in [("RELEASE_TAG", "release_tag"), ("UPSTREAM_SHA", "upstream_sha"),
                               ("PATCH_DIGEST", "patch_digest")]:
        if os.environ.get(environment) != metadata[field]:
            raise ValueError(f"Release provenance mismatch: {field}")
    seen = set()
    for line in (root / "SHA256SUMS").read_text().splitlines():
        expected, name = line.split("  ", 1)
        if Path(name).name != name or name in seen:
            raise ValueError("Invalid checksum filename")
        seen.add(name)
        with (root / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError(f"Checksum mismatch: {name}")
    files = {p.name for p in root.iterdir() if p.is_file() and p.name != "SHA256SUMS"}
    required = {"build-metadata.json", "patched-brave-core.tar.gz", "LICENSE",
                "Update-BraveChromeSync.ps1", "Start-BraveChromeSync.ps1", installer}
    if not required <= files:
        raise ValueError("Release is missing source, license, or updater scripts")
    if seen != files:
        raise ValueError("Missing checksums")
    archives = list(root.glob("*.zip"))
    if len(archives) != 1:
        raise ValueError("Expected one browser ZIP")
    with zipfile.ZipFile(archives[0]) as archive:
        if archive.testzip() is not None:
            raise ValueError("Browser archive is corrupt")
        names = archive.namelist()
        browsers = [name for name in names if PurePosixPath(name).name.lower() in ("brave.exe", "chrome.exe")]
        if len(browsers) != 1:
            raise ValueError("Missing or ambiguous browser executable")
        app = PurePosixPath(browsers[0]).parent
        for name in ("chrome.dll", "SyncUpdater.exe", "sync-release.json"):
            if (app / name).as_posix() not in names:
                raise ValueError(f"Browser archive is missing {name} beside its executable")
        marker = (app / "sync-release.json").as_posix()
        if json.loads(archive.read(marker)) != {"release_tag": metadata["release_tag"],
                                               "channel": metadata["channel"], "protocol": 1}:
            raise ValueError("Browser archive update identity differs from provenance")
    notes = (
        f"Windows x64 installer and portable build based on Brave **{metadata['upstream_tag']}** "
        f"(Chromium {metadata['chromium_version']}).\n\n"
        "The restored Google sign-in and Sync patch set was tested successfully by its author. "
        "Use your working Google API/OAuth credentials through runtime environment variables. "
        "The packaged browser passed a headless startup test. Google Sync must still be "
        "checked with your account and runtime credentials on this new version.\n\n"
        "Install the **-setup.exe** asset for shortcuts and automatic GitHub updates. "
        "About Brave checks, downloads and verifies updates, then offers Relaunch. "
        "Background checks run at startup and every six hours when Windows permits the scheduled task. "
        "The installer uses its own profile and preserves it across updates; no PowerShell or .NET installation is required. "
        "The ZIP and PowerShell scripts remain available for portable use.\n\n"
        "Includes SHA256SUMS, build-metadata.json, and patched-brave-core.tar.gz. "
        "Binaries are unsigned; PGO/LTO and debug symbols are disabled to reduce build resources.\n\n"
        f"Source: https://github.com/brave/brave-core/tree/{metadata['upstream_sha']}\n"
        f"Pipeline: https://github.com/{os.environ.get('GITHUB_REPOSITORY', 'Loukious/brave-chrome-sync')}"
        f"/commit/{metadata['pipeline_commit']}\n"
    )
    (root / "release-notes.md").write_text(notes, encoding="utf-8")
    print("Release payload verified")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))

"""Check provenance, checksums and archive contents before publishing."""
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile


def verify(root):
    metadata = json.loads((root / "build-metadata.json").read_text())
    if not metadata.get("browser_smoke_test"):
        raise ValueError("Packaged browser was not smoke tested")
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
                "Update-BraveChromeSync.ps1", "Start-BraveChromeSync.ps1"}
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
        names = [Path(name).name.lower() for name in archive.namelist()]
        if sum(name in ("brave.exe", "chrome.exe") for name in names) != 1:
            raise ValueError("Missing or ambiguous browser executable")
        if "chrome.dll" not in names:
            raise ValueError("Browser archive is missing chrome.dll")
    notes = (
        f"Windows x64 portable build based on Brave **{metadata['upstream_tag']}** "
        f"(Chromium {metadata['chromium_version']}).\n\n"
        "The restored Google sign-in and Sync patch set was tested successfully by its author. "
        "Use your working Google API/OAuth credentials through runtime environment variables. "
        "The packaged browser passed a headless startup test. Google Sync must still be "
        "checked with your account and runtime credentials on this new version.\n\n"
        "Native Brave browser updating is disabled for this portable build. "
        "Use the included PowerShell 7 launcher/updater for GitHub updates. "
        "Existing Brave profiles are not used by the launcher.\n\n"
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

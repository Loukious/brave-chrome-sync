"""Create lossless Windows checkpoints and validate Cargo's vendored inputs."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def verify_wasm_vendor(brave):
    vendor = brave / "third_party/wasm/vendor"
    manifests = sorted(vendor.glob("*/.cargo-checksum.json"))
    if not manifests:
        raise ValueError(f"Missing vendored Cargo checksum manifests: {vendor}")
    count = 0
    for manifest in manifests:
        for name, expected in json.loads(manifest.read_text(encoding="utf-8"))["files"].items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Invalid Cargo checksum path: {name}")
            path = manifest.parent / relative
            if not path.is_file():
                raise ValueError(f"Missing vendored Cargo input: {path}")
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                raise ValueError(f"Vendored Cargo checksum mismatch: {path}")
            count += 1
    print(f"Verified {count} vendored Cargo files across {len(manifests)} crates", flush=True)
    return count


def create_archive(root, archive):
    root = root.resolve()
    archive = archive.resolve()
    if archive.is_relative_to(root):
        raise ValueError("Checkpoint output must be outside the archived tree")
    archive.parent.mkdir(parents=True, exist_ok=True)
    command = ["tar.exe", "-czf", "-", "--options", "gzip:compression-level=1", "-C", str(root), "."]
    print("+", " ".join(command), flush=True)
    diagnostics = archive.with_name("snapshot-stderr.log")
    # A pipe avoids Windows tar's false archive-self match against a source
    # file's ID. Python writes the binary stream without shell/text conversion.
    with archive.open("xb") as output, diagnostics.open("wb") as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
        try:
            with process.stdout:
                shutil.copyfileobj(process.stdout, output, length=1024 * 1024)
            result = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
    warnings = diagnostics.read_text(encoding="utf-8", errors="replace").strip()
    if result != 0 or warnings:
        raise RuntimeError(f"Checkpoint creation was not lossless (exit {result}): {warnings}")
    print(f"Checkpoint: {archive.stat().st_size / 2**30:.2f} GiB", flush=True)

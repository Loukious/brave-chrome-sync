"""Create lossless Windows checkpoints and validate Cargo's vendored inputs."""
import hashlib
import gzip
import json
from pathlib import Path
import subprocess
import tarfile


def validate_archive(archive):
    """Read every tar member and gzip trailer, including data after tar EOF."""
    class Reader:
        def __init__(self, stream):
            self.stream = stream
            self.tail = b""
            self.size = 0

        def read(self, size=-1):
            data = self.stream.read(size)
            self.tail = (self.tail + data)[-1024:]
            self.size += len(data)
            return data

    try:
        with gzip.open(archive, "rb") as compressed:
            reader = Reader(compressed)
            with tarfile.open(fileobj=reader, mode="r|") as stream:
                count = sum(1 for _ in stream)
            while reader.read(1024 * 1024):
                pass
            if not count or reader.size % 512 or reader.tail != b"\0" * 1024:
                raise ValueError("Missing tar end markers")
    except (EOFError, OSError, tarfile.TarError, ValueError) as error:
        raise ValueError(f"Invalid checkpoint archive: {error}") from error
    print(f"Validated checkpoint: {count} members, complete tar and gzip streams", flush=True)
    return count


def verify_checkpoint(archive, allow_legacy=False):
    manifest = archive.with_name("state-manifest.json")
    if not manifest.exists():
        if not allow_legacy:
            raise ValueError("Checkpoint integrity manifest is missing")
        print("Legacy recovery: validating the entire archive before extraction", flush=True)
        return validate_archive(archive)
    expected = json.loads(manifest.read_text(encoding="utf-8"))
    if expected.get("format") != 1 or archive.stat().st_size != expected.get("bytes"):
        raise ValueError("Checkpoint size/format mismatch; download is incomplete")
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected.get("sha256"):
        raise ValueError("Checkpoint SHA256 mismatch; refusing extraction")
    count = validate_archive(archive)
    if count != expected.get("members"):
        raise ValueError("Checkpoint member count mismatch")
    return count


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
    checksum = hashlib.sha256()
    with archive.open("xb") as output, diagnostics.open("wb") as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
        try:
            with process.stdout:
                while chunk := process.stdout.read(1024 * 1024):
                    output.write(chunk)
                    checksum.update(chunk)
            result = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
    warnings = diagnostics.read_text(encoding="utf-8", errors="replace").strip()
    if result != 0 or warnings:
        raise RuntimeError(f"Checkpoint creation was not lossless (exit {result}): {warnings}")
    count = validate_archive(archive)
    archive.with_name("state-manifest.json").write_text(json.dumps({
        "format": 1, "bytes": archive.stat().st_size,
        "sha256": checksum.hexdigest(), "members": count,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Checkpoint: {archive.stat().st_size / 2**30:.2f} GiB", flush=True)

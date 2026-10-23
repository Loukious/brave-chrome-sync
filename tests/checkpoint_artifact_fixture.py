"""Round-trip a multi-range checkpoint through GitHub's real artifact storage."""
import argparse
import hashlib
import os
from pathlib import Path
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from checkpoint import create_archive, verify_checkpoint
from download_checkpoint import download

ROOT = Path(__file__).resolve().parents[1] / "work/checkpoint-transport-fixture"
NAME = "checkpoint-987654"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["create", "download"])
    args = parser.parse_args()
    source = ROOT / "source/payload.bin"
    if args.mode == "create":
        source.parent.mkdir(parents=True)
        # Reproducible incompressible input, larger than two production ranges.
        chunk = hashlib.shake_256(b"checkpoint transport fixture").digest(1024**2)
        with source.open("xb") as output:
            for _ in range(128):
                output.write(chunk)
        create_archive(source.parent, ROOT / "upload/state.tar.gz")
        if (ROOT / "upload/state.tar.gz").stat().st_size <= 128 * 1024**2:
            raise ValueError("Fixture must span multiple production download ranges")
        return
    destination = ROOT / "download"
    download(os.environ["GITHUB_REPOSITORY"], int(os.environ["GITHUB_RUN_ID"]), NAME, destination)
    verify_checkpoint(destination / "state.tar.gz")
    with tarfile.open(destination / "state.tar.gz", "r:gz") as archive, \
         archive.extractfile("./payload.bin") as actual, source.open("rb") as expected:
        if hashlib.file_digest(actual, "sha256").digest() != hashlib.file_digest(expected, "sha256").digest():
            raise ValueError("Real GitHub artifact round trip changed the fixture bytes")
    print("Real GitHub artifact round trip passed: parallel ranges, ZIP and checkpoint SHA256, payload bytes")


if __name__ == "__main__":
    main()

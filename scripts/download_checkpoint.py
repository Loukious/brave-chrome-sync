"""Resume large artifact transfers without forwarding GitHub credentials to storage."""
import argparse
import hashlib
import http.client
import os
from pathlib import Path
import re
import shutil
import time
import urllib.error
import urllib.request
import zipfile

from upstream import api


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def signed_url(repository, artifact_id):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}/zip",
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                 "Accept": "application/vnd.github+json", "User-Agent": "brave-chrome-sync"})
    # Handle the redirect explicitly so the API token never reaches blob storage.
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=120):
            raise ValueError("Artifact API did not return a download redirect")
    except urllib.error.HTTPError as error:
        try:
            location = error.headers.get("Location", "")
            if error.code != 302:
                raise
            if not location.startswith("https://"):
                raise ValueError("Artifact download redirect must use HTTPS")
            return location
        finally:
            error.close()


def transfer(path, size, get_url, attempts=8):
    """Keep completed bytes after disconnects; refresh the signed URL on retry."""
    with path.open("xb"):
        pass
    for attempt in range(attempts):
        offset = path.stat().st_size
        if offset == size:
            return
        try:
            request = urllib.request.Request(get_url(), headers={
                "Range": f"bytes={offset}-{size - 1}", "Accept-Encoding": "identity"})
            with urllib.request.urlopen(request, timeout=120) as response:
                expected = f"bytes {offset}-{size - 1}/{size}"
                if response.status != 206 or response.headers.get("Content-Range") != expected:
                    raise ValueError("Storage returned an unexpected byte range; refusing to append")
                with path.open("ab") as output:
                    while output.tell() < size:
                        chunk = response.read(min(1024 * 1024, size - output.tell()))
                        if not chunk:
                            raise ConnectionError("Incomplete artifact transfer")
                        output.write(chunk)
                        if output.tell() // (1024**3) != (output.tell() - len(chunk)) // (1024**3):
                            print(f"Downloaded {output.tell() / 1024**3:.1f} / {size / 1024**3:.1f} GiB", flush=True)
            return
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            # Never log exception text: it can contain the signed URL.
            reason = f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else type(error).__name__
            offset = path.stat().st_size
            print(f"Artifact transfer interrupted ({reason}); retained {offset} of {size} bytes "
                  f"(attempt {attempt + 1}/{attempts})", flush=True)
            if attempt + 1 == attempts:
                raise RuntimeError("Artifact transfer exhausted resumable retries") from None
            time.sleep(min(5 * (attempt + 1), 30))


def unpack(path, destination, expected_digest):
    with path.open("rb") as stream:
        actual = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected_digest:
        raise ValueError("Artifact SHA256 mismatch; refusing extraction")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if ("state.tar.gz" not in names or len(names) != len(set(names)) or
                set(names) - {"state.tar.gz", "state-manifest.json"}):
            raise ValueError("Unexpected checkpoint artifact members")
        for name in names:
            # Streaming extraction also checks each ZIP member's CRC.
            with archive.open(name) as source, (destination / name).open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
    # Only our transport scratch file inside the CI workspace is disposable.
    path.unlink()
    print("Artifact SHA256 and ZIP checks passed; checkpoint ready for tar/manifest verification", flush=True)


def download(repository, run_id, name, destination):
    matches = []
    page = 1
    while True:
        artifacts = api(f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100&page={page}")["artifacts"]
        matches.extend(item for item in artifacts if item["name"] == name)
        if len(artifacts) < 100:
            break
        page += 1
    if len(matches) != 1 or matches[0]["expired"]:
        raise ValueError("Expected one unexpired checkpoint artifact")
    artifact = matches[0]
    digest = artifact.get("digest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or artifact["size_in_bytes"] <= 0:
        raise ValueError("Artifact size or SHA256 metadata is missing")
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("Checkpoint download directory must be empty")
    path = destination / f"artifact-{artifact['id']}.zip.part"
    print(f"Downloading {name} (artifact {artifact['id']}, {artifact['size_in_bytes']} bytes) with resume", flush=True)
    transfer(path, artifact["size_in_bytes"], lambda: signed_url(repository, artifact["id"]))
    unpack(path, destination, digest)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--name", required=True)
    parser.add_argument("--path", default="checkpoint", type=Path)
    args = parser.parse_args()
    if args.run_id <= 0 or not re.fullmatch(r"checkpoint-\d+", args.name):
        raise ValueError("Invalid checkpoint run or name")
    download(os.environ["GITHUB_REPOSITORY"], args.run_id, args.name, args.path)


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        # HTTP errors can include signed credentials in their URL.
        status = f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else type(error).__name__
        raise SystemExit(f"Checkpoint download failed ({status})") from None

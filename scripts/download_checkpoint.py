"""Resume large artifact transfers without forwarding GitHub credentials to storage."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import http.client
import os
from pathlib import Path
import re
import shutil
import threading
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


def transfer(path, size, get_url, attempts=8, *, workers=8, range_size=64 * 1024**2,
             socket_timeout=30, rate_window=30, minimum_rate=256 * 1024, retry_delay=5):
    """Download disjoint ranges concurrently, resuming each after slow/failed reads.

    File length is preallocated and is never a completion indicator. Every range
    must finish, then unpack() verifies GitHub's whole-artifact SHA256.
    """
    if size <= 0 or workers <= 0 or range_size <= 0 or attempts <= 0:
        raise ValueError("Invalid artifact transfer size or concurrency")
    with path.open("xb") as output:
        output.truncate(size)
    stopped = threading.Event()
    url_lock, progress_lock = threading.Lock(), threading.Lock()
    cached_url, url_time = "", 0
    received, reported, report_time = 0, 0, time.monotonic()

    def current_url(refresh):
        nonlocal cached_url, url_time
        with url_lock:
            if refresh or not cached_url or time.monotonic() - url_time >= 45:
                cached_url, url_time = get_url(), time.monotonic()
            return cached_url

    def progress(count):
        nonlocal received, reported, report_time
        with progress_lock:
            received += count
            now = time.monotonic()
            if received - reported >= 1024**3 or now - report_time >= 30 or received == size:
                print(f"Downloaded {received / 1024**3:.2f} / {size / 1024**3:.2f} GiB "
                      f"({(received - reported) / max(now - report_time, .001) / 1024**2:.1f} MiB/s)", flush=True)
                reported, report_time = received, now

    def download_range(start, end):
        offset = start
        with path.open("r+b", buffering=0) as output:
            output.seek(offset)
            for attempt in range(attempts):
                if offset > end:
                    return
                if stopped.is_set():
                    return
                try:
                    request = urllib.request.Request(current_url(attempt > 0), headers={
                        "Range": f"bytes={offset}-{end}", "Accept-Encoding": "identity"})
                    with urllib.request.urlopen(request, timeout=socket_timeout) as response:
                        expected = f"bytes {offset}-{end}/{size}"
                        length = response.headers.get("Content-Length")
                        if (response.status != 206 or response.headers.get("Content-Range") != expected or
                                (length is not None and length != str(end - offset + 1))):
                            raise ValueError("Storage returned an unexpected byte range; refusing to write")
                        output.seek(offset)
                        window_start, window_offset = time.monotonic(), offset
                        while offset <= end:
                            if stopped.is_set():
                                return
                            # read1 returns available bytes; read(n) can block on a
                            # trickling connection indefinitely despite socket timeouts.
                            chunk = response.read1(min(1024 * 1024, end - offset + 1))
                            if not chunk:
                                raise ConnectionError("Incomplete artifact transfer")
                            if output.write(chunk) != len(chunk):
                                raise ValueError("Short artifact file write")
                            offset += len(chunk)
                            progress(len(chunk))
                            elapsed = time.monotonic() - window_start
                            if elapsed >= rate_window and offset <= end:
                                if (offset - window_offset) / elapsed < minimum_rate:
                                    raise TimeoutError("Artifact connection is too slow")
                                window_start, window_offset = time.monotonic(), offset
                    return
                except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
                    # Never log exception text: it can contain the signed URL.
                    reason = f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else type(error).__name__
                    print(f"Artifact range {start}-{end} interrupted ({reason}); resuming at {offset} "
                          f"(attempt {attempt + 1}/{attempts})", flush=True)
                    if attempt + 1 == attempts:
                        raise RuntimeError("Artifact transfer exhausted resumable retries") from None
                    if stopped.wait(min(retry_delay * (attempt + 1), 30)):
                        return

    print(f"Downloading with up to {workers} connections and {range_size / 1024**2:g} MiB ranges", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(download_range, start, min(start + range_size, size) - 1)
                   for start in range(0, size, range_size)]
        try:
            for future in as_completed(futures):
                future.result()
        except BaseException:
            stopped.set()
            for future in futures:
                future.cancel()
            raise
    if received != size:
        raise ValueError("Artifact transfer did not complete every range")


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

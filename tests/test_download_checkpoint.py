import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from download_checkpoint import download, signed_url, transfer, unpack


class DownloadTests(unittest.TestCase):
    def test_disconnect_resumes_bytes_and_checks_integrity(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("state.tar.gz", b"archive bytes" * 300)
            archive.writestr("state-manifest.json", b"{}")
        data = buffer.getvalue()
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.headers["Range"], self.headers.get("Authorization")))
                offset = int(self.headers["Range"].split("=")[1].split("-")[0])
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {offset}-{len(data) - 1}/{len(data)}")
                self.send_header("Content-Length", str(len(data) - offset))
                self.end_headers()
                # Drop the first socket mid-response; the next request must resume.
                self.wfile.write(data[:101] if len(requests) == 1 else data[offset:])
                self.close_connection = True

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                destination = Path(folder)
                artifact = {"id": 123, "name": "checkpoint-0", "expired": False,
                            "size_in_bytes": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
                with patch("download_checkpoint.api", return_value={"artifacts": [artifact]}), \
                     patch("download_checkpoint.signed_url", return_value=f"http://127.0.0.1:{server.server_port}/?secret=value") as urls, \
                     patch("download_checkpoint.time.sleep"):
                    download("owner/repo", 123, "checkpoint-0", destination)
                self.assertEqual(requests, [(f"bytes=0-{len(data)-1}", None),
                                            (f"bytes=101-{len(data)-1}", None)])
                self.assertEqual(urls.call_count, 2)
                self.assertEqual((destination / "state.tar.gz").read_bytes(), b"archive bytes" * 300)
                self.assertEqual(sorted(p.name for p in destination.iterdir()), ["state-manifest.json", "state.tar.gz"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_ignored_range_is_rejected_before_writing(self):
        response = io.BytesIO(b"wrong content")
        response.status = 200
        response.headers = {}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "download"
            with patch("download_checkpoint.urllib.request.urlopen", return_value=response):
                with self.assertRaisesRegex(ValueError, "unexpected byte range"):
                    transfer(path, 10, lambda: "https://storage/?secret=value")
                self.assertEqual(path.read_bytes(), bytes(10))

    def test_retry_budget_and_logs_do_not_expose_signed_url(self):
        error = urllib.error.HTTPError("https://storage/?secret=value", 403, "signed secret", {}, None)
        with tempfile.TemporaryDirectory() as folder:
            log = io.StringIO()
            with patch("download_checkpoint.urllib.request.urlopen", side_effect=error), \
                 patch("download_checkpoint.time.sleep"), contextlib.redirect_stdout(log):
                with self.assertRaisesRegex(RuntimeError, "exhausted resumable retries"):
                    transfer(Path(folder) / "download", 10, lambda: "https://storage/?secret=value",
                             attempts=2, retry_delay=0)
            self.assertIn("HTTP 403", log.getvalue())
            self.assertNotIn("secret", log.getvalue())

    @contextlib.contextmanager
    def storage(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/?secret=value"
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_parallel_ranges_write_exact_bytes_and_reuse_signed_url(self):
        data = bytes(range(256)) * 4097
        requests = []
        barrier = threading.Barrier(4)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                start, end = map(int, self.headers["Range"].removeprefix("bytes=").split("-"))
                requests.append((start, end, self.headers.get("Authorization")))
                # A serial implementation cannot pass this barrier.
                if start < 4 * 65536:
                    barrier.wait(timeout=5)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                self.wfile.write(data[start:end + 1])

            def log_message(self, *args):
                pass

        with self.storage(Handler) as url, tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "artifact"
            with patch("download_checkpoint.signed_url", return_value=url) as urls:
                transfer(path, len(data), urls, workers=4, range_size=65536)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(urls.call_count, 1)
            self.assertEqual(sorted(requests), [(start, min(start + 65536, len(data)) - 1, None)
                                               for start in range(0, len(data), 65536)])

    def test_slow_trickling_connection_is_replaced_and_resumed(self):
        data = b"0123456789" * 100
        offsets = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                start, end = map(int, self.headers["Range"].removeprefix("bytes=").split("-"))
                offsets.append(start)
                first = len(offsets) == 1
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                if first:
                    try:
                        for value in data:
                            self.wfile.write(bytes([value]))
                            self.wfile.flush()
                            time.sleep(.01)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        pass
                else:
                    self.wfile.write(data[start:end + 1])

            def log_message(self, *args):
                pass

        with self.storage(Handler) as url, tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "artifact"
            transfer(path, len(data), lambda: url, attempts=2, workers=1,
                     rate_window=.04, minimum_rate=10000, retry_delay=0)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(len(offsets), 2)
            self.assertEqual(offsets[0], 0)
            self.assertGreater(offsets[1], 0)
            self.assertLess(offsets[1], len(data))

    def test_failed_range_never_unpacks_preallocated_artifact(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(206)
                # Wrong range response must fail before writing anything.
                self.send_header("Content-Range", "bytes 0-9/20")
                self.end_headers()
                self.wfile.write(b"incorrect!")

            def log_message(self, *args):
                pass

        artifact = {"id": 1, "name": "checkpoint-0", "expired": False,
                    "size_in_bytes": 20, "digest": "sha256:" + "a" * 64}
        with self.storage(Handler) as url, tempfile.TemporaryDirectory() as folder, \
             patch("download_checkpoint.api", return_value={"artifacts": [artifact]}), \
             patch("download_checkpoint.signed_url", return_value=url), \
             patch("download_checkpoint.unpack") as extract:
            with self.assertRaisesRegex(ValueError, "unexpected byte range"):
                download("owner/repo", 1, "checkpoint-0", Path(folder))
            extract.assert_not_called()
            self.assertEqual((Path(folder) / "artifact-1.zip.part").read_bytes(), bytes(20))

    def test_connection_that_stops_sending_times_out_and_retries(self):
        data = b"complete artifact"
        offsets = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                start = int(self.headers["Range"].split("=")[1].split("-")[0])
                offsets.append(start)
                first = len(offsets) == 1
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                self.send_header("Content-Length", str(len(data) - start))
                self.end_headers()
                if first:
                    self.wfile.write(data[:3])
                    self.wfile.flush()
                    time.sleep(.2)
                else:
                    self.wfile.write(data[start:])

            def log_message(self, *args):
                pass

        with self.storage(Handler) as url, tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "artifact"
            transfer(path, len(data), lambda: url, attempts=2, workers=1,
                     socket_timeout=.05, retry_delay=0)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(offsets, [0, 3])

    def test_digest_and_zip_members_fail_closed(self):
        for names, digest_valid in [(["state.tar.gz"], False),
                                    (["state.tar.gz", "../escape"], True),
                                    (["state.tar.gz", "state.tar.gz"], True)]:
            with self.subTest(names=names, digest_valid=digest_valid), tempfile.TemporaryDirectory() as folder:
                destination = Path(folder)
                path = destination / "download.zip"
                with zipfile.ZipFile(path, "w") as archive:
                    for name in names:
                        archive.writestr(name, b"payload")
                digest = "sha256:" + (hashlib.sha256(path.read_bytes()).hexdigest() if digest_valid else "0" * 64)
                with self.assertRaises(ValueError):
                    unpack(path, destination, digest)
                self.assertEqual(list(destination.iterdir()), [path])

    def test_signed_url_redirect_keeps_authorization_on_api_only(self):
        error = urllib.error.HTTPError("https://api.github.com", 302, "Found",
                                     {"Location": "https://storage/?secret=value"}, None)
        with patch.dict("os.environ", {"GH_TOKEN": "api-token"}), \
             patch("download_checkpoint.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = error
            self.assertEqual(signed_url("owner/repo", 12), "https://storage/?secret=value")
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer api-token")
            self.assertEqual(request.host, "api.github.com")

    def test_missing_digest_expired_or_duplicate_artifact_is_rejected(self):
        good = {"id": 1, "name": "checkpoint-0", "expired": False,
                "size_in_bytes": 10, "digest": "sha256:" + "a" * 64}
        for artifacts in [[], [good, good], [{**good, "expired": True}], [{**good, "digest": ""}]]:
            with self.subTest(artifacts=artifacts), tempfile.TemporaryDirectory() as folder, \
                 patch("download_checkpoint.api", return_value={"artifacts": artifacts}), \
                 patch("download_checkpoint.transfer") as network:
                with self.assertRaises(ValueError):
                    download("owner/repo", 123, "checkpoint-0", Path(folder))
                network.assert_not_called()


if __name__ == "__main__":
    unittest.main()

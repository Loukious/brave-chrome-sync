import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from checkpoint import create_archive, verify_wasm_vendor


class CheckpointTests(unittest.TestCase):
    def fixture(self, root):
        crate = root / "third_party/wasm/vendor/serde_core"
        crate.mkdir(parents=True)
        files = {"LICENSE-MIT": b"license\n", "src/lib.rs": b"pub fn fixture() {}\n"}
        for name, data in files.items():
            path = crate / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (crate / ".cargo-checksum.json").write_text(json.dumps({
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
            "package": "fixture"}))
        return crate

    def test_missing_license_and_corruption_fail_before_compilation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            crate = self.fixture(root)
            self.assertEqual(verify_wasm_vendor(root), 2)
            (crate / "LICENSE-MIT").unlink()
            with self.assertRaisesRegex(ValueError, "Missing.*LICENSE-MIT"):
                verify_wasm_vendor(root)
            (crate / "LICENSE-MIT").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_wasm_vendor(root)

    def test_empty_vendor_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "Missing.*manifests"):
                verify_wasm_vendor(Path(temporary))

    def test_tar_warning_is_fatal_even_with_zero_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "input"
            root.mkdir()
            archive = Path(temporary) / "checkpoint/state.tar.gz"

            def fake_tar(command, **options):
                self.assertEqual(command[2], "-")
                self.assertEqual(options["stdout"], subprocess.PIPE)
                options["stderr"].write(b"tar.exe: LICENSE-MIT: Can't add archive to itself\n")
                process = unittest.mock.Mock()
                process.stdout = io.BytesIO(b"partial archive")
                process.wait.return_value = 0
                return process

            with patch("checkpoint.subprocess.Popen", side_effect=fake_tar):
                with self.assertRaisesRegex(RuntimeError, "not lossless.*archive to itself"):
                    create_archive(root, archive)

    def test_output_inside_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "outside"):
                create_archive(root, root / "state.tar.gz")

    @unittest.skipUnless(os.name == "nt", "Uses the Windows tar executable")
    def test_windows_binary_archive_round_trip_preserves_sources_and_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            crate = self.fixture(source)
            binary = bytes(range(256)) * 4096
            (source / "binary.dat").write_bytes(binary)
            (source / ".hidden").write_bytes(b"hidden\n")
            os.link(crate / "LICENSE-MIT", source / "LICENSE-copy")
            timestamp = 1700000000
            os.utime(crate / "LICENSE-MIT", (timestamp, timestamp))
            create_archive(source, base / "checkpoint/state.tar.gz")
            with tarfile.open(base / "checkpoint/state.tar.gz", "r:gz") as stream:
                self.assertIn("./third_party/wasm/vendor/serde_core/LICENSE-MIT", stream.getnames())
            restored = base / "restored"
            restored.mkdir()
            subprocess.run(["tar.exe", "-xzf", str(base / "checkpoint/state.tar.gz"), "-C", str(restored)], check=True)
            self.assertEqual(verify_wasm_vendor(restored), 2)
            self.assertEqual((restored / "binary.dat").read_bytes(), binary)
            self.assertEqual((restored / ".hidden").read_bytes(), b"hidden\n")
            self.assertEqual((restored / "LICENSE-copy").read_bytes(), b"license\n")
            self.assertEqual(int((restored / "third_party/wasm/vendor/serde_core/LICENSE-MIT").stat().st_mtime), timestamp)


if __name__ == "__main__":
    unittest.main()

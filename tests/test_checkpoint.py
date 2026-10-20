import hashlib
import gzip
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
from checkpoint import create_archive, verify_wasm_vendor, verify_checkpoint, validate_archive, extract_archive


class CheckpointTests(unittest.TestCase):
    def test_unicode_restore_preserves_exact_names_timestamps_and_native_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            archive = base / "state.tar.gz"
            names = ["ascii.txt", "caf\u00e9.txt", "\u4e2d\u6587.txt", "\U0001f428.txt", "e\u0301.txt"]
            with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as stream:
                # The target does not exist when this directory link is restored.
                for name, target, kind in [("directory-link", "target", "dir"),
                                           ("broken-directory-link", "missing", "dir")]:
                    member = tarfile.TarInfo(name)
                    member.type = tarfile.SYMTYPE
                    member.linkname = target
                    member.pax_headers = {"LIBARCHIVE.symlinktype": kind}
                    stream.addfile(member)
                directory = tarfile.TarInfo("target")
                directory.type = tarfile.DIRTYPE
                directory.pax_headers = {"mtime": "1700000000.1234567"}
                stream.addfile(directory)
                for index, name in enumerate(names):
                    member = tarfile.TarInfo("target/" + name)
                    member.size = 1
                    member.pax_headers = {"mtime": "1700000000.1234567"}
                    stream.addfile(member, io.BytesIO(bytes([index])))
                for name, target, kind in [("hard-link", "target/\u4e2d\u6587.txt", tarfile.LNKTYPE),
                                           ("file-link", "target/\u4e2d\u6587.txt", tarfile.SYMTYPE)]:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = target
                    member.pax_headers = {"LIBARCHIVE.symlinktype": "file", "mtime": "1700000000.1234567"}
                    stream.addfile(member)
            restored = base / "restored"
            extract_archive(archive, restored)
            self.assertEqual({p.name for p in (restored / "target").iterdir()}, set(names))
            self.assertEqual((restored / "target").stat().st_mtime_ns, 1700000000123456700)
            for index, name in enumerate(names):
                self.assertEqual((restored / "target" / name).read_bytes(), bytes([index]))
                self.assertEqual((restored / "target" / name).stat().st_mtime_ns, 1700000000123456700)
            self.assertTrue(os.path.samefile(restored / "hard-link", restored / "target/\u4e2d\u6587.txt"))
            self.assertTrue((restored / "directory-link").is_symlink())
            self.assertTrue((restored / "directory-link").is_dir())
            self.assertEqual((restored / "directory-link/\u4e2d\u6587.txt").read_bytes(), bytes([2]))
            self.assertTrue((restored / "file-link").is_symlink())
            self.assertEqual((restored / "file-link").read_bytes(), bytes([2]))
            broken = restored / "broken-directory-link"
            self.assertTrue(broken.is_symlink())
            if os.name == "nt":
                import stat
                self.assertTrue(broken.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_DIRECTORY)

    def test_restore_rejects_escaping_paths_links_and_existing_tree(self):
        for name, kind, target in [("../escape", tarfile.REGTYPE, ""),
                                   ("link", tarfile.SYMTYPE, "../escape"),
                                   ("link", tarfile.LNKTYPE, "../escape"),
                                   ("pipe", tarfile.FIFOTYPE, "")]:
            with self.subTest(name=name, kind=kind), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                archive = base / "state.tar.gz"
                with tarfile.open(archive, "w:gz") as stream:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = target
                    stream.addfile(member)
                with self.assertRaises(tarfile.FilterError):
                    extract_archive(archive, base / "restored")
                self.assertFalse((base / "escape").exists())
                with self.assertRaisesRegex(ValueError, "existing build tree"):
                    extract_archive(archive, base / "restored")

    def test_restore_does_not_replace_symlinks_with_copies(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            archive = base / "state.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                stream.addfile(tarfile.TarInfo("target"))
                link = tarfile.TarInfo("link")
                link.type = tarfile.SYMTYPE
                link.linkname = "target"
                stream.addfile(link)
            with patch("checkpoint.os.symlink", side_effect=PermissionError("Denied")):
                with self.assertRaises(PermissionError):
                    extract_archive(archive, base / "restored")
            self.assertFalse((base / "restored/link").exists())

    def archive(self, folder):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as stream:
            entry = tarfile.TarInfo("fixture.bin")
            entry.size = 4096
            stream.addfile(entry, io.BytesIO(b"x" * entry.size))
        archive = Path(folder) / "state.tar.gz"
        archive.write_bytes(gzip.compress(buffer.getvalue()))
        return archive, buffer.getvalue()

    def test_complete_gzip_with_truncated_tar_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, data = self.archive(temporary)
            archive.write_bytes(gzip.compress(data[:1024]))
            with self.assertRaisesRegex(ValueError, "Invalid checkpoint"):
                validate_archive(archive)

    def test_truncated_gzip_and_missing_tar_eof_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, data = self.archive(temporary)
            archive.write_bytes(archive.read_bytes()[:-6])
            with self.assertRaisesRegex(ValueError, "Invalid checkpoint"):
                validate_archive(archive)
            archive.write_bytes(gzip.compress(data[:4608]))
            with self.assertRaisesRegex(ValueError, "Missing tar end"):
                validate_archive(archive)
            # Zero-filled payload cannot masquerade as the two EOF blocks.
            archive.write_bytes(gzip.compress(data[:512] + b"\0" * 4096))
            with self.assertRaisesRegex(ValueError, "Missing tar end"):
                validate_archive(archive)

    def test_manifest_checks_size_digest_and_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, _ = self.archive(temporary)
            manifest = {"format": 1, "bytes": archive.stat().st_size,
                        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "members": 1}
            path = archive.with_name("state-manifest.json")
            path.write_text(json.dumps(manifest))
            self.assertEqual(verify_checkpoint(archive), 1)
            for key, value, error in [("bytes", 1, "size/format"),
                                      ("sha256", "bad", "SHA256"), ("members", 2, "member count")]:
                path.write_text(json.dumps({**manifest, key: value}))
                with self.assertRaisesRegex(ValueError, error):
                    verify_checkpoint(archive)

    def test_legacy_archive_requires_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, _ = self.archive(temporary)
            with self.assertRaisesRegex(ValueError, "manifest is missing"):
                verify_checkpoint(archive)
            self.assertEqual(verify_checkpoint(archive, allow_legacy=True), 1)

    def test_invalid_checkpoint_is_rejected_before_creating_build_tree(self):
        from stage import restore
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = Path(temporary) / "checkpoint"
            checkpoint.mkdir()
            archive, data = self.archive(checkpoint)
            archive.write_bytes(gzip.compress(data[:1024]))
            root = Path(temporary) / "build"
            with patch("stage.extract_archive") as extract:
                with self.assertRaisesRegex(ValueError, "Invalid checkpoint"):
                    restore(root, checkpoint, allow_legacy=True)
                extract.assert_not_called()
                self.assertFalse(root.exists())

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
            os.symlink("third_party/wasm/vendor/serde_core", source / "directory-link", target_is_directory=True)
            os.symlink("third_party/wasm/vendor/serde_core/LICENSE-MIT", source / "file-link")
            timestamp = 1700000000123456700
            os.utime(crate / "LICENSE-MIT", ns=(timestamp, timestamp))
            create_archive(source, base / "checkpoint/state.tar.gz")
            self.assertEqual(verify_checkpoint(base / "checkpoint/state.tar.gz"), 14)
            with tarfile.open(base / "checkpoint/state.tar.gz", "r:gz") as stream:
                self.assertIn("./third_party/wasm/vendor/serde_core/LICENSE-MIT", stream.getnames())
                self.assertEqual(stream.getmember("./directory-link").pax_headers["LIBARCHIVE.symlinktype"], "dir")
            restored = base / "restored"
            extract_archive(base / "checkpoint/state.tar.gz", restored)
            self.assertEqual(verify_wasm_vendor(restored), 2)
            self.assertEqual((restored / "binary.dat").read_bytes(), binary)
            self.assertEqual((restored / ".hidden").read_bytes(), b"hidden\n")
            self.assertEqual((restored / "LICENSE-copy").read_bytes(), b"license\n")
            self.assertTrue(os.path.samefile(restored / "LICENSE-copy", restored / "third_party/wasm/vendor/serde_core/LICENSE-MIT"))
            self.assertTrue((restored / "directory-link").is_symlink())
            self.assertTrue((restored / "directory-link").is_dir())
            self.assertTrue((restored / "file-link").is_symlink())
            self.assertEqual((restored / "directory-link/LICENSE-MIT").read_bytes(), b"license\n")
            self.assertEqual((restored / "file-link").read_bytes(), b"license\n")
            self.assertEqual((restored / "third_party/wasm/vendor/serde_core/LICENSE-MIT").stat().st_mtime_ns, timestamp)


if __name__ == "__main__":
    unittest.main()

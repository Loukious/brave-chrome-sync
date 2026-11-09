import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from prepare import source_root, validate_layout, write_environment
from checkpoint import create_archive, verify_checkpoint, extract_archive


class PrepareLayoutTests(unittest.TestCase):
    def test_configured_cache_is_outside_checkout_and_inside_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            brave = source_root(root) / "brave"
            brave.mkdir(parents=True)
            with patch("prepare.service_key", return_value="fixture"):
                write_environment(brave, root)
            config = dict(line.split("=", 1) for line in (brave / ".env").read_text().splitlines())
            cache = Path(json.loads(config["cache_dir"])) / "git"
            checkout = brave.parent.parent
            self.assertTrue(cache.is_relative_to(root))
            self.assertTrue(checkout.is_relative_to(root))
            self.assertFalse(cache.is_relative_to(checkout))
            self.assertFalse(checkout.is_relative_to(cache))
            self.assertEqual(checkout / "src", source_root(root))

    def test_cache_link_back_into_checkout_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root(root).mkdir(parents=True)
            os.symlink(root / "checkout", root / "cache", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "outside"):
                validate_layout(root)

    def test_git_alternates_and_build_output_survive_checkpoint_restore(self):
        work = ROOT / "work"
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as directory:
            base = Path(directory).resolve()
            root = base / "build"
            cache = root / "cache/git/repository.git"
            source = source_root(root)
            seed = base / "seed"

            def git(*args, cwd=None):
                return subprocess.check_output(["git", *map(str, args)], cwd=cwd,
                                               stderr=subprocess.STDOUT)

            git("init", seed)
            (seed / "input.txt").write_text("shared Git object\n")
            git("add", "input.txt", cwd=seed)
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-m", "fixture", cwd=seed)
            git("clone", "--bare", seed, cache)
            git("clone", "--shared", cache, source)
            self.assertTrue((source / ".git/objects/info/alternates").is_file())
            output = source / "out/Sync/fixture.obj"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"compiled fixture")
            stamp = output.stat().st_mtime_ns
            validate_layout(root)
            archive = base / "checkpoint/state.tar.gz"
            create_archive(root, archive)
            # Preserve the original fixture and restore at the same absolute
            # path, exactly as the next hosted runner does.
            root.rename(base / "original")
            verify_checkpoint(archive, restore_root=root)
            extract_archive(archive, root)
            validate_layout(root)
            git("fsck", "--full", cwd=source)
            self.assertEqual(git("show", "HEAD:input.txt", cwd=source), b"shared Git object\n")
            self.assertEqual(output.read_bytes(), b"compiled fixture")
            self.assertEqual(output.stat().st_mtime_ns, stamp)


if __name__ == "__main__":
    unittest.main()

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from extension_services import UBLOCK_ID, check_build_key, parse_manifest, service_key
from prepare import write_environment


class ExtensionServiceTests(unittest.TestCase):
    def manifest(self, url, extension_id=UBLOCK_ID):
        return json.dumps({"gupdate": {"app": [{"appid": extension_id,
                            "updatecheck": {"codebase": url}}]}}).encode()

    def test_original_id_and_brave_download_host_required(self):
        url = "https://brave-core-ext.s3.brave.com/release/uBlock.crx"
        self.assertEqual(parse_manifest(self.manifest(url)), url)
        for bad in ["http://brave-core-ext.s3.brave.com/u.crx",
                    "https://brave.com.attacker.example/u.crx",
                    "https://user:password@brave-core-ext.s3.brave.com/u.crx",
                    "https://brave-core-ext.s3.brave.com:8080/u.crx"]:
            with self.subTest(url=bad), self.assertRaises(ValueError):
                parse_manifest(self.manifest(bad))
        with self.assertRaises(ValueError):
            parse_manifest(self.manifest(url, "different-extension"))
        with self.assertRaises(ValueError):
            parse_manifest(b" " * 4097)

    def test_missing_key_stops_build_before_writing_environment(self):
        with patch.dict(os.environ, {"BRAVE_SERVICES_KEY": ""}), tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                write_environment(root, root)
            self.assertFalse((root / ".env").exists())
        for key in ["bad\nheader", "bad\x00header"]:
            with patch("extension_services.os.environ", {"BRAVE_SERVICES_KEY": key}), self.assertRaises(ValueError):
                service_key()

    def test_configured_key_reaches_environment_and_matches_compiled_flag(self):
        key = "fixture-service-key"
        with patch.dict(os.environ, {"BRAVE_SERVICES_KEY": key}), tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_environment(root, root)
            values = dict(line.split("=", 1) for line in (root / ".env").read_text().splitlines())
            self.assertEqual(json.loads(values["brave_services_key"]), key)
            header = root / "gen/brave/components/constants/brave_services_key.h"
            header.parent.mkdir(parents=True)
            header.write_text('#define BUILDFLAG_INTERNAL_BRAVE_SERVICES_KEY() ("fixture-service-key")\n')
            check_build_key(root)
            header.write_text('#define BUILDFLAG_INTERNAL_BRAVE_SERVICES_KEY() ("")\n')
            with self.assertRaises(ValueError):
                check_build_key(root)


if __name__ == "__main__":
    unittest.main()

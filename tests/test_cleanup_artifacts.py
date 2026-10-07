import contextlib
import io
import os
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cleanup_artifacts import main


class CleanupTests(unittest.TestCase):
    def invoke(self, arguments, listing=None, responses=None):
        listing = listing if listing is not None else {"artifacts": [
            {"name": "checkpoint-5", "id": 5}, {"name": "checkpoint-6", "id": 6},
            {"name": "checkpoint-7", "id": 7}]}
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "123",
                                     "GH_TOKEN": "fixture"}), \
             patch.object(sys, "argv", ["cleanup_artifacts.py", *arguments]), \
             patch("cleanup_artifacts.api", side_effect=listing if isinstance(listing, list) else None,
                   return_value=listing) as api, \
             patch("cleanup_artifacts.urllib.request.urlopen", side_effect=responses) as delete, \
             patch("cleanup_artifacts.time.sleep") as sleep, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            main()
            return api, delete, sleep, output.getvalue()

    def error(self, status):
        return urllib.error.HTTPError("https://api.github.com/fixture", status, "fixture", {}, None)

    def test_server_error_is_retried_and_only_named_current_run_artifact_is_deleted(self):
        response = unittest.mock.MagicMock()
        api, delete, sleep, _ = self.invoke(["--name", "checkpoint-5"],
                                          responses=[self.error(500), self.error(503), response])
        api.assert_called_once_with("repos/owner/repo/actions/runs/123/artifacts?per_page=100")
        self.assertEqual(delete.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])
        for call in delete.call_args_list:
            self.assertEqual(call.args[0].full_url, "https://api.github.com/repos/owner/repo/actions/artifacts/5")
            self.assertEqual(call.args[0].method, "DELETE")

    def test_persistent_api_failure_is_nonfatal_for_best_effort_cleanup(self):
        _, delete, sleep, output = self.invoke(["--name", "checkpoint-5", "--best-effort"],
                                             responses=[self.error(500)] * 3)
        self.assertEqual(delete.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertIn("::warning::Artifact cleanup deferred (HTTP 500)", output)

    def test_listing_network_failure_is_retried_and_deferred(self):
        api, delete, _, output = self.invoke(["--name", "checkpoint-5", "--best-effort"],
                                            listing=[urllib.error.URLError("offline")] * 3)
        self.assertEqual(api.call_count, 3)
        delete.assert_not_called()
        self.assertIn("cleanup deferred", output)

    def test_already_deleted_artifact_is_success_and_permission_errors_are_not_retried(self):
        _, delete, sleep, _ = self.invoke(["--name", "checkpoint-5"], responses=[self.error(404)])
        delete.assert_called_once()
        sleep.assert_not_called()
        _, delete, sleep, output = self.invoke(["--name", "checkpoint-5", "--best-effort"],
                                             responses=[self.error(403)])
        delete.assert_called_once()
        sleep.assert_not_called()
        self.assertIn("HTTP 403", output)

    def test_strict_cleanup_preserves_error_and_invalid_names_are_rejected(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.invoke(["--name", "checkpoint-5"], responses=[self.error(500)] * 3)
        with self.assertRaisesRegex(ValueError, "unrelated artifact"):
            self.invoke(["--name", "unrelated", "--best-effort"])


if __name__ == "__main__":
    unittest.main()

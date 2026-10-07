"""Remove only explicitly named disposable artifacts of the current workflow run."""
import argparse
import os
import time
import urllib.error
import urllib.request

from upstream import api


def retry(operation):
    for attempt in range(3):
        try:
            return operation()
        except (urllib.error.URLError, TimeoutError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code not in (408, 429) and error.code < 500:
                raise
            if attempt == 2:
                raise
            delay = 2 ** (attempt + 1)
            print(f"GitHub cleanup request failed; retrying in {delay}s", flush=True)
            time.sleep(delay)


def remove_artifact(repository, artifact_id):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}",
        method="DELETE", headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                                  "Accept": "application/vnd.github+json",
                                  "User-Agent": "brave-chrome-sync"})
    try:
        with urllib.request.urlopen(request, timeout=60):
            pass
    except urllib.error.HTTPError as error:
        # A previous DELETE can succeed even when its response is lost.
        if error.code != 404:
            raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--best-effort", action="store_true")
    args = parser.parse_args()
    if args.name != "browser-release" and not (
        args.name.startswith("checkpoint-") and args.name.removeprefix("checkpoint-").isdigit()
    ):
        raise ValueError("Refusing to delete an unrelated artifact")
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = int(os.environ["GITHUB_RUN_ID"])
    path = f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100"
    try:
        for artifact in retry(lambda: api(path))["artifacts"]:
            if artifact["name"] != args.name:
                continue
            retry(lambda: remove_artifact(repository, artifact["id"]))
            print(f"Removed consumed artifact: {args.name}")
    except (urllib.error.URLError, TimeoutError) as error:
        if not args.best_effort:
            raise
        status = f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else type(error).__name__
        print(f"::warning::Artifact cleanup deferred ({status}); retained artifacts will expire automatically.", flush=True)


if __name__ == "__main__":
    main()

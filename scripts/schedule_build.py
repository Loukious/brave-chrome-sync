"""Dispatch the daily build only when no build or checkpoint recovery is pending."""
import json
import os
import urllib.request

from upstream import api


WORKFLOW = "build-release.yml"


def active_build(repository):
    page = 1
    while True:
        runs = api(f"repos/{repository}/actions/workflows/{WORKFLOW}/runs?per_page=100&page={page}")["workflow_runs"]
        for run in runs:
            # Include recovery branches and every nonterminal status, not just
            # in_progress: a concurrency-pending or queued build also owns the slot.
            if run["status"] != "completed":
                return run
        if len(runs) < 100:
            return None
        page += 1


def dispatch(repository):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/actions/workflows/{WORKFLOW}/dispatches",
        data=json.dumps({"ref": "main"}).encode(), method="POST",
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                 "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                 "User-Agent": "brave-chrome-sync"})
    # Do not blindly retry a POST: a lost response may still have started a build.
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 204:
            raise RuntimeError(f"Unexpected workflow dispatch status: {response.status}")


def schedule(repository):
    if run := active_build(repository):
        print(f"Skipping automatic build: run {run['id']} is {run['status']}", flush=True)
        return False
    dispatch(repository)
    print("Started the daily browser build on main", flush=True)
    return True


if __name__ == "__main__":
    schedule(os.environ["GITHUB_REPOSITORY"])

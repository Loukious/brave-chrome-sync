"""Remove only explicitly named disposable artifacts of the current workflow run."""
import argparse
import os
import urllib.request

from upstream import api


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    if args.name != "browser-release" and not (
        args.name.startswith("checkpoint-") and args.name.removeprefix("checkpoint-").isdigit()
    ):
        raise ValueError("Refusing to delete an unrelated artifact")
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = int(os.environ["GITHUB_RUN_ID"])
    path = f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100"
    for artifact in api(path)["artifacts"]:
        if artifact["name"] != args.name:
            continue
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact['id']}",
            method="DELETE", headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                                      "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=60):
            print(f"Removed consumed artifact: {args.name}")


if __name__ == "__main__":
    main()

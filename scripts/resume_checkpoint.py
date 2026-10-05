"""Resume only trusted checkpoints with unchanged browser source/build inputs."""
import argparse
import hashlib
import json
import os
from pathlib import PurePosixPath, PureWindowsPath
import re
import subprocess

from upstream import ROOT, api


def git_file(commit, path):
    return subprocess.run(["git", "show", f"{commit}:{path}"], cwd=ROOT,
                          check=True, capture_output=True).stdout


def inputs(read):
    config = json.loads(read("config.json"))
    names = [line.strip() for line in read("patches/series").decode().splitlines()
             if line.strip() and not line.startswith("#")]
    if not names or len(names) != len(set(names)) or any(
            PurePosixPath(name).name != name or not name.endswith(".patch") for name in names):
        raise ValueError("Invalid checkpoint patch series")
    source = [".gitattributes", "patches/series", *[f"patches/{name}" for name in names],
              "scripts/prepare.py", "scripts/runner-setup.ps1"]
    identity = hashlib.sha256()
    build = {key: config[key] for key in
             ("gn_args", "release_channel", "runner", "node_version", "pnpm_version")}
    identity.update(json.dumps(build, sort_keys=True).encode())
    for path in source:
        identity.update(path.encode() + b"\0" + read(path) + b"\0")
    return identity.hexdigest(), names


def original_digest(commit, names, windows_order=False):
    """Reconstruct a pipeline hash, including the historical Windows ordering."""
    paths = [".gitattributes", "config.json", "patches/series",
             *[f"patches/{name}" for name in names]]
    tracked = subprocess.run(["git", "ls-tree", "-r", "--name-only", commit], cwd=ROOT,
                             check=True, capture_output=True, text=True).stdout.splitlines()
    for folder, extension in [("scripts", ".py"), ("scripts", ".mjs"), ("scripts", ".ps1"),
                              (".github/workflows", ".yml"), ("updater", ".cs"),
                              ("updater", ".csproj"), ("installer", ".iss")]:
        paths += sorted((path for path in tracked if str(PurePosixPath(path).parent) == folder
                         and path.endswith(extension)),
                        key=PureWindowsPath if windows_order else PurePosixPath)
    identity = hashlib.sha256()
    for path in paths:
        identity.update(path.encode() + b"\0" + git_file(commit, path) + b"\0")
    return identity.hexdigest()


def check_compatibility(commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid checkpoint pipeline commit")
    old, names = inputs(lambda path: git_file(commit, path))
    current, _ = inputs(lambda path: (ROOT / path).read_bytes())
    if old != current:
        raise ValueError("Checkpoint browser patches, toolchain or build settings have changed")
    return {original_digest(commit, names), original_digest(commit, names, windows_order=True)}


def check_run(run_id, repository):
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError("Invalid checkpoint workflow run ID")
    run = api(f"repos/{repository}/actions/runs/{run_id}")
    if (run["head_repository"]["full_name"] != repository or
            run["event"] not in ("push", "schedule", "workflow_dispatch") or
            run["path"].split("@")[0] != ".github/workflows/build-release.yml"):
        raise ValueError("Checkpoint was not produced by this repository's trusted build workflow")
    commit = run["head_sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid checkpoint pipeline commit")
    subprocess.run(["git", "fetch", "--depth=1", "origin", commit], cwd=ROOT, check=True)
    check_compatibility(commit)
    return commit


def check_marker(marker, tag, sha, current_digest, resume_commit=""):
    expected_digests = check_compatibility(resume_commit) if resume_commit else {current_digest}
    if not any(marker == {"upstream_tag": tag, "upstream_sha": sha, "patch_digest": identity}
               for identity in expected_digests):
        raise ValueError(f"Checkpoint does not match this upstream version and patch set: "
                         f"expected tag={tag}, sha={sha}, digests={sorted(expected_digests)}; actual={marker}")
    return {**marker, "patch_digest": current_digest}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = parser.parse_args()
    commit = check_run(args.run_id, args.repository)
    print(f"Compatible browser checkpoint from pipeline {commit}")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"commit={commit}\n")


if __name__ == "__main__":
    main()

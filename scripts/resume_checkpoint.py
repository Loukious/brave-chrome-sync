"""Resume trusted checkpoints, replaying appended build-time fixes before building."""
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


def appended_patches(old_read, current_read):
    old, old_names = inputs(old_read)
    current, current_names = inputs(current_read)
    if old == current:
        return []
    if current_names[:len(old_names)] != old_names or len(current_names) <= len(old_names):
        raise ValueError("Checkpoint browser patches, toolchain or build settings have changed")
    # Existing patches and initialization/toolchain inputs must remain identical.
    def prefix_read(path):
        return old_read(path) if path == "patches/series" else current_read(path)
    if inputs(prefix_read)[0] != old:
        raise ValueError("Checkpoint browser patches, toolchain or build settings have changed")
    extra = current_names[len(old_names):]
    for name in extra:
        patch = current_read(f"patches/{name}").decode()
        headers = re.findall(r"(?m)^diff --git (.+)$", patch)
        files = [re.fullmatch(r"a/(\S+) b/(\S+)", header) for header in headers]
        if not files or any(match is None for match in files):
            raise ValueError("Checkpoint recovery only supports appended Brave C++ patches")
        for a, b in (match.groups() for match in files):
            cpp = a.startswith(("browser/", "components/")) and a.endswith((".cc", ".h"))
            template = a.startswith("chromium_src/") and a.endswith(".html.ts.lit_mangler.ts")
            registration = a == "chromium_src/chrome/installer/util/sync_browser_registration.h"
            if a != b or "\\" in a or ".." in PurePosixPath(a).parts or not (cpp or template or registration):
                raise ValueError("Checkpoint recovery only supports appended Brave C++ or Lit template patches")
    return extra


def check_compatibility(commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid checkpoint pipeline commit")
    old_read = lambda path: git_file(commit, path)
    _, names = inputs(old_read)
    appended_patches(old_read, lambda path: (ROOT / path).read_bytes())
    return {original_digest(commit, names), original_digest(commit, names, windows_order=True)}


def apply_checkpoint_patches(root, commit):
    if not commit:
        return
    for name in appended_patches(lambda path: git_file(commit, path),
                                 lambda path: (ROOT / path).read_bytes()):
        content = (ROOT / "patches" / name).read_text()
        templates = []
        for name_in_patch in re.findall(r"(?m)^diff --git a/(\S+) b/\S+$", content):
            if name_in_patch.endswith(".html.ts.lit_mangler.ts"):
                native = root / "src" / name_in_patch.removeprefix("chromium_src/").removesuffix(".lit_mangler.ts")
                if not native.is_file():
                    raise ValueError(f"Incremental template input does not exist: {native}")
                templates.append(native)
        print(f"Replaying incremental browser fix: {name}", flush=True)
        subprocess.run(["git", "am", "--keep-cr", str(ROOT / "patches" / name)],
                       cwd=root / "src/brave", check=True)
        # A removed mangler is absent from Brave's normal override scan. Its
        # native input must still invalidate previously generated Settings HTML.
        for native in templates:
            os.utime(native, None)


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

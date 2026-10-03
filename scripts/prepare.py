"""Create an isolated Brave checkout and replay the maintained patch series."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from upstream import ROOT, series
from extension_services import service_key


def run(*args, cwd=None):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def checkout(target, tag, sha, source=None):
    if target.exists():
        raise ValueError(f"Checkout already exists; refusing to overwrite: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if source:
        run("git", "clone", "--shared", "--no-checkout", source, target)
        run("git", "checkout", "--detach", sha, cwd=target)
    else:
        run("git", "init", target)
        run("git", "remote", "add", "origin", "https://github.com/brave/brave-core.git", cwd=target)
        run("git", "fetch", "--depth=1", "origin", f"refs/tags/{tag}", cwd=target)
        run("git", "checkout", "--detach", "FETCH_HEAD", cwd=target)
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=target, text=True).strip()
    if actual != sha:
        raise ValueError(f"Upstream tag moved: expected {sha}, fetched {actual}")
    run("git", "config", "user.name", "Brave Chrome Sync build", cwd=target)
    run("git", "config", "user.email", "build@users.noreply.github.com", cwd=target)
    run("git", "config", "core.autocrlf", "false", cwd=target)
    for patch in series():
        # Fail on conflicts; never silently drop a customization.
        run("git", "am", "--keep-cr", patch, cwd=target)


def write_environment(target, root):
    environment = {
        "use_brave_hermetic_toolchain": False,
        "is_brave_release_build": 1,
        "ignore_patch_version_number": False,
        "projects_chrome_custom_vars_checkout_pgo_profiles": False,
        "cache_dir": str(root / "cache").replace("\\", "/"),
        "projects_chrome_custom_vars_checkout_clangd": False,
        "projects_chrome_custom_vars_checkout_clang_coverage_tools": False,
        "projects_chrome_custom_vars_checkout_android": False,
        "projects_chrome_custom_vars_checkout_ios": False,
        "brave_services_key": service_key(),
    }
    (target / ".env").write_text("".join(f"{key}={json.dumps(value)}\n"
                                         for key, value in environment.items()), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--patch-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    target = root / "src/brave"
    checkout(target, args.tag, args.sha, args.source)
    if args.patch_only:
        return
    write_environment(target, root)
    pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    if not pnpm:
        raise ValueError("pnpm is not installed")
    os.environ["DEPOT_TOOLS_WIN_TOOLCHAIN"] = "0"
    # Keep pnpm's global store in the checkpoint as its symlinks must survive.
    run(pnpm, "config", "set", "store-dir", str(root / "pnpm-store"), "--location=project", cwd=target)
    run(pnpm, "run", "init", "--no-history", "--target_os=win", "--target_arch=x64", cwd=target)
    (root / "prepared.json").write_text(json.dumps({"upstream_tag": args.tag,
                                                   "upstream_sha": args.sha}), encoding="utf-8")


if __name__ == "__main__":
    main()

"""Bound compilation time and preserve source timestamps between Windows jobs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request
import zipfile
from datetime import datetime, timezone

from prepare import run
from upstream import ROOT, digest
from package_windows import package_browser
from checkpoint import create_archive, verify_wasm_vendor, verify_checkpoint
from resume_checkpoint import check_marker
from extension_services import check_build_key, check_service
from windows_build_process import run_build


def timed_run(command, cwd, seconds):
    print("+", " ".join(map(str, command)), flush=True)
    return run_build(command, cwd, seconds)


def output(key, value):
    print(f"{key}={value}", flush=True)
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(f"{key}={value}\n")


def snapshot(root, checkpoint):
    checkpoint.mkdir(parents=True, exist_ok=True)
    # tar preserves hidden files, timestamps and pnpm symlinks. Keeping the whole
    # initialized tree avoids touching inputs and recompiling everything next job.
    verify_wasm_vendor(root / "src/brave")
    create_archive(root, checkpoint / "state.tar.gz")


def restore(root, checkpoint, allow_legacy=False):
    if root.exists():
        raise ValueError(f"Refusing to restore over existing build tree: {root}")
    verify_checkpoint(checkpoint / "state.tar.gz", allow_legacy=allow_legacy)
    root.mkdir(parents=True)
    run("tar.exe", "-xzf", checkpoint / "state.tar.gz", "-C", root)
    verify_wasm_vendor(root / "src/brave")
    # The downloaded checkpoint is disposable input inside this repository's
    # CI workspace; it is not a compiler cache or build output directory.
    (checkpoint / "state.tar.gz").unlink()
    (checkpoint / "state-manifest.json").unlink(missing_ok=True)


def package(root, dist, tag, sha, release_tag, channel):
    check_build_key(root / "src/out/Sync")
    extension_service = check_service(tag)
    dist.mkdir(parents=True, exist_ok=True)
    candidates = list((root / "src/out/Sync/dist").glob("*.zip"))
    if len(candidates) != 1:
        raise ValueError(f"Expected one browser archive, found: {candidates}")
    destination, installer = package_browser(candidates[0], dist, release_tag, channel,
                                             tag, datetime.now(timezone.utc).isoformat())
    smoke = smoke_test(root, destination)
    installer_smoke = installer_smoke_test(root, installer, release_tag, channel)
    target = root / "src/brave"
    run("git", "archive", "--format=tar.gz", "--prefix=brave-core/",
        f"--output={dist / 'patched-brave-core.tar.gz'}", "HEAD", cwd=target)
    config = json.loads((ROOT / "config.json").read_text())
    package_json = json.loads((target / "package.json").read_text())
    metadata = {"upstream_tag": tag, "upstream_sha": sha,
                "upstream_source_channel": config["upstream_channel"],
                "chromium_version": package_json["config"]["projects"]["chrome"]["tag"],
                "release_tag": release_tag, "channel": channel,
                "patch_digest": digest(), "gn_args": config["gn_args"],
                "pipeline_commit": os.environ.get("GITHUB_SHA", "local"),
                "workflow_run": os.environ.get("GITHUB_RUN_ID", "local"),
                "google_credentials": "runtime only", "native_updater": True,
                "updater_protocol": 1, "installer": installer.name,
                "extension_service_manifest_test": extension_service,
                "devtools_target_smoke_test": True,
                "shared_default_profile_smoke_test": True,
                "taskbar_launch_smoke_test": True,
                "default_browser_registration_test": True,
                "installer_smoke_test": installer_smoke, "browser_smoke_test": smoke}
    (dist / "build-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(ROOT / "scripts/Update-BraveChromeSync.ps1", dist)
    shutil.copy2(ROOT / "scripts/Start-BraveChromeSync.ps1", dist)
    shutil.copy2(ROOT / "LICENSE", dist)
    sums = []
    for path in sorted(dist.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS":
            with path.open("rb") as stream:
                sums.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {path.name}\n")
    (dist / "SHA256SUMS").write_text("".join(sums), encoding="utf-8")


def smoke_test(root, archive):
    app = root / "smoke-app"
    profile = root / "smoke-profile"
    with zipfile.ZipFile(archive) as stream:
        stream.extractall(app)
    executables = list(app.rglob("brave.exe")) + list(app.rglob("chrome.exe"))
    if len(executables) != 1:
        raise ValueError("Packaged browser executable is missing or ambiguous")
    result = launch_smoke_test(executables[0], profile)
    run("node", ROOT / "scripts/smoke_devtools.mjs", executables[0], root / "devtools-smoke-profile")
    return result


def installer_smoke_test(root, installer, release_tag, channel):
    install = root / "installer-smoke"
    run(installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS",
        f"/DIR={install}", f"/LOG={root / 'installer-smoke.log'}")
    identity = json.loads((install / "installation.json").read_text())
    pointer = json.loads((install / "current.json").read_text())
    if identity != {"repository": "Loukious/brave-chrome-sync", "channel": channel, "protocol": 1} or pointer["tag"] != release_tag:
        raise ValueError("Installer did not initialize the correct browser identity")
    run("pwsh", "-NoProfile", "-File", ROOT / "tests/browser-registration.test.ps1",
        "-InstallRoot", install, "-Channel", channel)
    # Launch through the same stable executable used by shortcuts, with an isolated profile.
    result = launch_smoke_test(install / "SyncBrowser.exe", root / "installer-smoke-profile",
                              launcher=True)
    run("node", ROOT / "scripts/smoke_profile.mjs", install / pointer["browser"],
        install / "SyncBrowser.exe", root / "profile-smoke-localappdata")
    run("node", ROOT / "scripts/smoke_taskbar.mjs", install / pointer["browser"],
        install / "SyncBrowser.exe", root / "taskbar-smoke-profile", channel)
    run("pwsh", "-NoProfile", "-File", ROOT / "tests/taskbar-pins.test.ps1",
        "-Helper", (install / pointer["browser"]).parent / "SyncUpdater.exe")
    return result


def launch_smoke_test(executable, profile, launcher=False):
    process = subprocess.Popen([str(executable), "--headless", "--no-first-run",
                                "--disable-gpu", "--disable-background-networking",
                                "--remote-debugging-port=0", f"--user-data-dir={profile}",
                                "about:blank"])
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if not launcher and process.poll() is not None:
                raise RuntimeError(f"Packaged browser exited early: {process.returncode}")
            port_file = profile / "DevToolsActivePort"
            if port_file.exists():
                port = int(port_file.read_text().splitlines()[0])
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=5) as response:
                    result = json.load(response)
                if "Browser" not in result:
                    raise ValueError("Browser smoke test returned no version")
                print(f"Browser smoke test passed: {result['Browser']}")
                return result["Browser"]
            time.sleep(1)
        raise RuntimeError("Packaged browser did not start its DevTools endpoint")
    finally:
        if launcher:
            # The shortcut launcher exits after starting the browser. Stop only
            # processes using this smoke test's explicit profile.
            command = ('Get-CimInstance Win32_Process | Where-Object { '
                       '$_.Name -in @("brave.exe", "chrome.exe") -and '
                       '$_.CommandLine -and $_.CommandLine.Contains($env:SMOKE_PROFILE) '
                       '} | ForEach-Object { taskkill /PID $_.ProcessId /T /F }')
            subprocess.run(["pwsh", "-NoProfile", "-Command", command],
                           env={**os.environ, "SMOKE_PROFILE": str(profile)}, check=True)
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=True)
            process.wait(timeout=30)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["prepare", "compile"], required=True)
    parser.add_argument("--root", type=Path, default=Path("D:/b"))
    parser.add_argument("--checkpoint", type=Path, default=ROOT / "checkpoint")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--channel", choices=["release", "beta", "nightly"], required=True)
    parser.add_argument("--resume-commit", default="")
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text())
    root = args.root.resolve()
    if root != Path("D:/b").resolve() or os.name != "nt":
        raise ValueError("CI checkpoints require the fixed, non-junction Windows path D:/b")
    if args.mode == "prepare":
        if root.exists():
            raise ValueError("D:/b already exists; use a fresh ephemeral runner")
        command = ["python", ROOT / "scripts/prepare.py", "--root", root,
                   "--tag", args.tag, "--sha", args.sha]
        result = timed_run(command, ROOT, 240 * 60)
        if result:
            raise RuntimeError(f"Source initialization failed (exit {result}); no release published")
        marker = json.loads((root / "prepared.json").read_text())
        marker["patch_digest"] = digest()
        (root / "prepared.json").write_text(json.dumps(marker), encoding="utf-8")
        snapshot(root, args.checkpoint)
        output("finished", "false")
        return
    restore(root, args.checkpoint, allow_legacy=bool(args.resume_commit))
    marker = json.loads((root / "prepared.json").read_text())
    marker = check_marker(marker, args.tag, args.sha, digest(), args.resume_commit)
    (root / "prepared.json").write_text(json.dumps(marker), encoding="utf-8")
    pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    if not pnpm:
        raise ValueError("pnpm is missing")
    # Release configuration without PGO/LTO or symbols reduces memory and disk use.
    command = [pnpm, "run", "build", "Release", "-C", "Sync", "--target_arch=x64",
               f"--channel={args.channel}", "--skip_signing", "--use_remoteexec=false",
               "--target=create_dist_zips", f"--ninja=j:{config['compile_jobs']}"]
    command += [f"--gn={key}:{json.dumps(value)}" for key, value in config["gn_args"].items()]
    # Brave only forwards its --ninja=j value into SISO_LIMITS for remoteexec
    # builds. Set the local limit explicitly for this offline compilation.
    os.environ["SISO_LIMITS"] = f"local={config['compile_jobs']}"
    result = timed_run(command, root / "src/brave", config["compile_minutes"] * 60)
    if result == 124:
        output_dir = root / "src/out/Sync"
        if (output_dir / "gn_gen.guard").exists() or not (output_dir / "build.ninja").exists():
            raise RuntimeError("GN generation did not finish within this stage; cannot resume compilation")
        # Brave cleans non-production CI builds when this sentinel survives an
        # interruption. This timeout is intentional; Ninja tracks unfinished work.
        (output_dir / "build.guard").unlink(missing_ok=True)
        snapshot(root, args.checkpoint)
        output("finished", "false")
    elif result == 0:
        package(root, args.dist.resolve(), args.tag, args.sha, args.release_tag, args.channel)
        output("finished", "true")
    else:
        raise RuntimeError(f"Compilation failed (exit {result}); no release published")


if __name__ == "__main__":
    main()

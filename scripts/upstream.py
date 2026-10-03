"""Resolve a published Brave version and immutable source/build identities."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def api(path):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "brave-chrome-sync"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"https://api.github.com/{path}", headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def version(tag):
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError(f"Invalid Brave version tag: {tag}")
    return tuple(map(int, match.groups()))


def select_release(releases, channel, minimum):
    prefix = {"release": "Release ", "beta": "Beta ", "nightly": "Nightly "}[channel]
    # Some Nightly releases are incorrectly marked non-prerelease upstream.
    candidates = [r for r in releases if not r.get("draft")
                  and r.get("name", "").startswith(prefix)
                  and TAG.fullmatch(r["tag_name"])
                  and version(r["tag_name"]) >= version("v" + minimum)]
    if not candidates:
        raise ValueError(f"No published {channel} release at or above {minimum}")
    return max(candidates, key=lambda r: version(r["tag_name"]))


def series():
    names = [line.strip() for line in (ROOT / "patches/series").read_text().splitlines()
             if line.strip() and not line.startswith("#")]
    if not names or len(names) != len(set(names)):
        raise ValueError("Patch series is empty or contains duplicates")
    for name in names:
        if Path(name).name != name or not name.endswith(".patch"):
            raise ValueError(f"Unsafe patch name: {name}")
        yield ROOT / "patches" / name


def digest():
    hash_value = hashlib.sha256()
    paths = [ROOT / ".gitattributes", ROOT / "config.json", ROOT / "patches/series", *series()]
    paths += sorted((ROOT / "scripts").glob("*.py"))
    paths += sorted((ROOT / "scripts").glob("*.ps1"))
    paths += sorted((ROOT / ".github/workflows").glob("*.yml"))
    paths += sorted((ROOT / "updater").glob("*.cs"))
    paths += sorted((ROOT / "updater").glob("*.csproj"))
    paths += sorted((ROOT / "installer").glob("*.iss"))
    for path in paths:
        hash_value.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        hash_value.update(path.read_bytes() + b"\0")
    return hash_value.hexdigest()


def resolve_commit(tag):
    obj = api(f"repos/brave/brave-core/git/ref/tags/{tag}")["object"]
    for _ in range(5):
        if obj["type"] == "commit":
            return obj["sha"]
        if obj["type"] != "tag":
            break
        obj = api(f"repos/brave/brave-core/git/tags/{obj['sha']}")["object"]
    raise ValueError("Upstream tag does not resolve to a commit")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text())
    if args.tag:
        version(args.tag)
        release = api(f"repos/brave/brave-browser/releases/tags/{args.tag}")
        if release["draft"]:
            raise ValueError("Cannot build an unpublished upstream release")
    else:
        releases = []
        # Stable can be buried below many daily Nightly releases.
        for page in range(1, 11):
            batch = api(f"repos/brave/brave-browser/releases?per_page=100&page={page}")
            releases.extend(batch)
            try:
                release = select_release(releases, config["upstream_channel"],
                                         config["minimum_upstream_version"])
                break
            except ValueError:
                if len(batch) < 100:
                    raise
        else:
            raise ValueError("No matching release in the last 1000 releases")
    tag = release["tag_name"]
    patch_digest = digest()
    release_tag = f"{tag}-sync.r{config['patch_revision']}.{patch_digest[:12]}"
    exists = False
    if args.repository:
        try:
            published = api(f"repos/{args.repository}/releases/tags/{release_tag}")
            exists = not published["draft"]
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    prefix = release.get("name", "")
    channel = "nightly" if prefix.startswith("Nightly ") else (
        "beta" if prefix.startswith("Beta ") else "release")
    result = {"build": str(not exists).lower(), "upstream_tag": tag,
              "upstream_sha": resolve_commit(tag), "release_tag": release_tag,
              "patch_digest": patch_digest, "channel": channel,
              "prerelease": str(channel != "release").lower(),
              "runner": config["runner"], "node_version": config["node_version"],
              "pnpm_version": config["pnpm_version"]}
    print(json.dumps(result, indent=2))
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            for key, value in result.items():
                stream.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()

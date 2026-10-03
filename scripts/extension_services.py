"""Check the original Brave extension service without logging its client key."""
import argparse
import base64
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

UBLOCK_ID = "jcokkipkhhgiakinbnnplhkdbjbgcgpe"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def service_key():
    key = os.environ.get("BRAVE_SERVICES_KEY", "").strip()
    if not key or any(c in key for c in "\r\n\x00"):
        raise ValueError("Configure the BRAVE_SERVICES_KEY Actions secret before building")
    return key


def parse_manifest(body, extension_id=UBLOCK_ID):
    if len(body) > 4096:
        raise ValueError("Extension manifest exceeds the browser's size limit")
    manifest = json.loads(body)
    apps = manifest.get("gupdate", {}).get("app", [])
    if not isinstance(apps, list):
        raise ValueError("Extension service returned an invalid app list")
    for app in apps:
        if isinstance(app, dict) and app.get("appid") == extension_id:
            url = app.get("updatecheck", {}).get("codebase", "")
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            if (parsed.scheme != "https" or not host.endswith(".brave.com")
                    or parsed.username or parsed.password or parsed.port not in (None, 443)):
                raise ValueError("Extension service returned an unexpected download host")
            return url
    raise ValueError("Extension service did not return the original uBlock Origin ID")


def check_build_key(output):
    header = output / "gen/brave/components/constants/brave_services_key.h"
    match = re.search(r'BUILDFLAG_INTERNAL_BRAVE_SERVICES_KEY\(\) \((".*")\)',
                      header.read_text(encoding="utf-8"))
    if not match or json.loads(match[1]) != service_key():
        raise ValueError("Compiled Brave service key does not match this build's configured key")


def check_service(tag):
    from upstream import api, version
    major, minor, build = version(tag)
    package = api(f"repos/brave/brave-core/contents/package.json?ref={tag}")
    config = json.loads(base64.b64decode(package["content"]))
    chromium_major = config["config"]["projects"]["chrome"]["tag"].split(".")[0]
    if not chromium_major.isdecimal():
        raise ValueError("Upstream returned an invalid Chromium version")
    query = {"response": "redirect", "os": "win", "arch": "x64",
             "prod": "chromiumcrx", "prodchannel": "",
             "prodversion": f"{chromium_major}.{major}.{minor}.{build}", "lang": "en-US",
             "acceptformat": "crx3,puff",
             "x": f"id={UBLOCK_ID}&installsource=ondemand&uc"}
    request = Request("https://go-updater.brave.com/extensions?" + urlencode(query),
                      headers={"BraveServiceKey": service_key(),
                               "Content-Type": "application/json",
                               "User-Agent": "brave-chrome-sync-build-check"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            body = response.read(4097)
    except HTTPError as error:
        # Do not include request headers or arbitrary server bodies in CI logs.
        raise RuntimeError(f"Brave extension service returned HTTP {error.code}; no browser build started") from None
    parse_manifest(body)
    print(f"Brave extension service passed: HTTP 200, original uBlock ID {UBLOCK_ID}")
    return {"status": 200, "extension_id": UBLOCK_ID}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    check_service(args.tag)


if __name__ == "__main__":
    main()

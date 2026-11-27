"""Fetch an exact Chromium source file for regression checks."""
import base64
import sys
from urllib.request import urlopen


def read_chromium_source(version, relative):
    gitiles = f"https://chromium.googlesource.com/chromium/src/+/refs/tags/{version}/{relative}?format=TEXT"
    mirror = f"https://raw.githubusercontent.com/chromium/chromium/{version}/{relative}"
    try:
        with urlopen(gitiles, timeout=30) as response:
            return base64.b64decode(response.read().strip(), validate=True)
    except (OSError, ValueError) as error:
        print(f"Chromium Gitiles unavailable for {relative} ({error}), trying GitHub", file=sys.stderr)
        with urlopen(mirror, timeout=30) as response:
            return response.read()


if __name__ == "__main__":
    sys.stdout.buffer.write(read_chromium_source(*sys.argv[1:]))

"""Exercise the real downloader against the local Postman storage simulation.

Start: postman simulation run postman/simulations/checkpoint-download.sim.yaml --port 4900 --output ndjson
Run: python tests/run_checkpoint_simulation.py
Socket disconnect/trickle tests live in test_download_checkpoint.py because they
need direct control of the underlying TCP connection.
"""
import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from download_checkpoint import transfer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:4900/storage")
    args = parser.parse_args()
    data = bytes(range(256)) * 4097
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "artifact"
        transfer(target, len(data), lambda: args.base_url + "/artifact", range_size=65536)
        if target.read_bytes() != data:
            raise ValueError("Simulation bytes differ from the fixture")
        try:
            transfer(Path(folder) / "bad", len(data), lambda: args.base_url + "/wrong-range",
                     range_size=65536)
        except ValueError as error:
            if "unexpected byte range" not in str(error):
                raise
        else:
            raise ValueError("Downloader accepted an invalid storage range")
    print("Postman storage simulation: parallel bytes match and invalid ranges fail closed")


if __name__ == "__main__":
    main()

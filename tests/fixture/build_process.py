"""Small process tree that models a compiler saving state after its parent exits."""
from pathlib import Path
import os
import signal
import subprocess
import sys
import time

mode, folder = sys.argv[1], Path(sys.argv[2])
if mode in ("child", "ignore"):
    def interrupt(number, frame):
        if mode == "ignore":
            return
        time.sleep(1)
        (folder / "state-saved").write_text("complete")
        sys.exit(0)

    signal.signal(signal.SIGBREAK, interrupt)
    (folder / "child-pid").write_text(str(os.getpid()))
    (folder / "ready").write_text("ready")
    while True:
        time.sleep(0.05)
else:
    signal.signal(signal.SIGBREAK, lambda number, frame: sys.exit(3))
    child = subprocess.Popen([sys.executable, __file__, "ignore" if mode == "parent-ignore" else "child", str(folder)])
    while not (folder / "ready").exists():
        time.sleep(0.01)
    while child.poll() is None:
        time.sleep(0.05)

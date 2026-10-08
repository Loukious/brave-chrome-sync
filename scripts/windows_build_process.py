"""Run a build in its own hidden console and owned Windows job object."""
import argparse
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import time

TIMEOUT = 124
UNSAFE_STOP = 125


def run_build(command, cwd, seconds, grace=120):
    if os.name != "nt":
        raise ValueError("Build supervision requires Windows")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    # An isolated console makes CTRL_BREAK available on headless Actions runners
    # without delivering it to the runner, the caller or unrelated processes.
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--seconds", str(seconds),
         "--grace", str(grace), "--cwd", str(cwd), "--", *map(str, command)],
        creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup,
        stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr)
    try:
        return process.wait(timeout=seconds + grace + 60)
    except subprocess.TimeoutExpired:
        # The supervisor owns a kill-on-close job containing only its build.
        process.kill()
        process.wait(timeout=30)
        raise RuntimeError("Build supervisor did not stop safely; refusing checkpoint")


class BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


class Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in
                ("user_time", "kernel_time", "period_user_time", "period_kernel_time")]
    _fields_ += [(name, wintypes.DWORD) for name in
                 ("page_faults", "total_processes", "active_processes", "terminated_processes")]


class BuildJob:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "GetCurrentProcess": ([], wintypes.HANDLE),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "GenerateConsoleCtrlEvent": ([wintypes.DWORD, wintypes.DWORD], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes = arguments
            function.restype = result
        self.handle = self.api.CreateJobObjectW(None, None)
        self.check(self.handle)
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        self.check(self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        # Assign the supervisor before launching anything, so every descendant
        # joins this job, including children created while their parents exit.
        self.check(self.api.AssignProcessToJobObject(self.handle, self.api.GetCurrentProcess()))
        # Keep the non-inheritable handle open until supervisor exit. Closing it
        # earlier would terminate the supervisor itself as well as its children.

    @staticmethod
    def check(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    def children_running(self):
        accounting = Accounting()
        self.check(self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                                                      ctypes.sizeof(accounting), None))
        return accounting.active_processes > 1  # Exclude this supervisor.

    def interrupt(self, group):
        self.check(self.api.GenerateConsoleCtrlEvent(1, group))  # CTRL_BREAK_EVENT

    def wait_for_children(self, grace):
        deadline = time.monotonic() + grace
        while self.children_running():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)
        return True


def supervise(command, cwd, seconds, grace):
    job = BuildJob()
    process = subprocess.Popen(command, cwd=cwd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    timed_out = False
    try:
        result = process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        print("Stage deadline reached: sending CTRL_BREAK to the owned build group", flush=True)
        # The root can exit in this interval. Job accounting still tracks its
        # descendants; an already-empty job requires no interrupt or taskkill.
        if job.children_running():
            try:
                job.interrupt(process.pid)
            except OSError:
                if job.children_running():
                    raise
        result = TIMEOUT
    if not job.wait_for_children(grace):
        print("Build did not stop gracefully; refusing checkpoint. Owned job will be terminated.", flush=True)
        return UNSAFE_STOP
    process.wait(timeout=5)
    if timed_out:
        print("Build group stopped; incremental state can now be checkpointed", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--grace", type=float, default=120)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.seconds <= 0 or args.grace <= 0:
        raise ValueError("Command and positive shutdown deadlines are required")
    return supervise(command, args.cwd, args.seconds, args.grace)


if __name__ == "__main__":
    sys.exit(main())

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from windows_build_process import run_build, supervise, TIMEOUT, UNSAFE_STOP
from stage import timed_run


class SupervisorTests(unittest.TestCase):
    def test_deadline_race_with_already_exited_child_does_not_fail(self):
        process = unittest.mock.Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("build", 1), 0]
        job = unittest.mock.Mock()
        job.children_running.return_value = False
        job.wait_for_children.return_value = True
        with patch("windows_build_process.BuildJob", return_value=job), \
             patch("windows_build_process.subprocess.Popen", return_value=process), \
             patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True):
            self.assertEqual(supervise(["build"], ".", 1, 5), TIMEOUT)
        job.interrupt.assert_not_called()
        job.wait_for_children.assert_called_once_with(5)

    def test_remaining_processes_prevent_checkpoint(self):
        process = unittest.mock.Mock()
        process.wait.side_effect = subprocess.TimeoutExpired("build", 1)
        job = unittest.mock.Mock()
        job.children_running.return_value = True
        job.wait_for_children.return_value = False
        with patch("windows_build_process.BuildJob", return_value=job), \
             patch("windows_build_process.subprocess.Popen", return_value=process), \
             patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True):
            self.assertEqual(supervise(["build"], ".", 1, 5), UNSAFE_STOP)
        job.interrupt.assert_called_once_with(process.pid)

    def test_compiler_errors_remain_fatal(self):
        process = unittest.mock.Mock()
        process.wait.return_value = 7
        job = unittest.mock.Mock()
        job.wait_for_children.return_value = True
        with patch("windows_build_process.BuildJob", return_value=job), \
             patch("windows_build_process.subprocess.Popen", return_value=process), \
             patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True):
            self.assertEqual(supervise(["build"], ".", 1, 5), 7)
        job.interrupt.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Requires real Windows console and job APIs")
class WindowsSupervisorTests(unittest.TestCase):
    def command(self, folder, mode="parent"):
        return [sys.executable, str(Path(__file__).parent / "fixture/build_process.py"), mode, str(folder)]

    def test_graceful_timeout_waits_for_grandchild_state_and_leaves_other_process_alive(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            try:
                self.assertEqual(timed_run(self.command(folder), folder, 2), TIMEOUT)
                self.assertEqual((folder / "state-saved").read_text(), "complete")
                self.assertIsNone(other.poll())
            finally:
                other.terminate()
                other.wait(timeout=10)

    def test_normal_exit_and_real_compiler_failure_are_preserved(self):
        for code in (0, 7):
            self.assertEqual(run_build([sys.executable, "-c", f"raise SystemExit({code})"],
                                       Path.cwd(), 5), code)

    def test_stuck_grandchild_is_terminated_and_checkpoint_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            self.assertEqual(run_build(self.command(folder, "parent-ignore"), folder, 2, grace=0.5), UNSAFE_STOP)
            self.assertFalse((folder / "state-saved").exists())
            process_id = int((folder / "child-pid").read_text())
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x100000, False, process_id)  # SYNCHRONIZE only
            if handle:
                try:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 1000), 0)
                finally:
                    kernel.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()

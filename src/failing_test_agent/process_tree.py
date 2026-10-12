"""Start a child process so that it and everything it spawns can be terminated together.

Windows has no SIGTERM/SIGKILL and no process-group kill, so the child is placed
in a Job Object. POSIX uses a new session and signals the process group.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 9

    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class ProcessTree:
    def __init__(self, argv: list[str], cwd: Path, env: dict[str, str]) -> None:
        self._job = None
        if sys.platform == "win32":
            self._job = _kernel32.CreateJobObjectW(None, None)
            if not self._job:
                raise ctypes.WinError(ctypes.get_last_error())
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _kernel32.SetInformationJobObject(
                self._job,
                _JobObjectExtendedLimitInformation,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                error = ctypes.WinError(ctypes.get_last_error())
                self.close()
                raise error
        try:
            self.process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
                ),
                start_new_session=sys.platform != "win32",
            )
        except BaseException:
            self.close()
            raise
        if self._job:
            # The child is assigned just after it starts; anything it spawns
            # from here on joins the job automatically.
            if not _kernel32.AssignProcessToJobObject(
                self._job, wintypes.HANDLE(int(self.process._handle))
            ):
                error = ctypes.WinError(ctypes.get_last_error())
                self.process.kill()
                self.close()
                raise error

    def request_stop(self) -> None:
        """Ask the tree to stop; callers follow up with kill() after a grace period."""
        try:
            if sys.platform == "win32":
                os.kill(self.process.pid, signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(self.process.pid, signal.SIGTERM)
        except OSError:
            pass

    def kill(self) -> None:
        if sys.platform == "win32":
            if self._job:
                _kernel32.TerminateJobObject(self._job, 1)
        else:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except OSError:
                pass

    def close(self) -> None:
        if self._job:
            _kernel32.CloseHandle(self._job)
            self._job = None

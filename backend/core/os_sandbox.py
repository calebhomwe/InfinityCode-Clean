"""OS-level process hardening for Windows (Stage 2.1).

Launches untrusted Python in a subprocess with:
  1. Restricted token (dangerous privileges stripped, admin groups deny-only)
  2. Low integrity level (cannot write to medium-integrity locations like
     Documents/Desktop/most of the user profile)
  3. Job Object with kill-on-close + commit-memory cap

If hardening cannot be applied (non-Windows, missing pywin32, unusual
security policy) the caller MUST fall back to the plain subprocess path and
log a warning -- never fail the mission over hardening being unavailable.

Network egress is NOT blocked here (requires AppContainer/WFP; follow-up).
"""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform.startswith("win")

# Privileges an untrusted code runner never needs.
_STRIP_PRIVILEGES = (
    "SeDebugPrivilege",
    "SeBackupPrivilege",
    "SeRestorePrivilege",
    "SeTakeOwnershipPrivilege",
    "SeLoadDriverPrivilege",
    "SeImpersonatePrivilege",
    "SeCreateTokenPrivilege",
    "SeTcbPrivilege",
)

_DENY_GROUPS = ("BUILTIN\\Administrators", "BUILTIN\\Power Users")


def can_harden() -> bool:
    """True when OS-level hardening is expected to work on this host."""
    if not _IS_WINDOWS:
        return False
    try:
        import win32job  # noqa: F401
        import win32process  # noqa: F401
        import win32security  # noqa: F401
        return True
    except ImportError:
        return False


def _make_restricted_low_token():
    """Restricted, low-integrity copy of the current process token."""
    import win32api
    import win32con
    import win32security

    tok = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_ALL_ACCESS)
    try:
        deny_sids = []
        for name in _DENY_GROUPS:
            try:
                sid = win32security.LookupAccountName(None, name)[0]
                deny_sids.append((sid, win32con.SE_GROUP_USE_FOR_DENY_ONLY))
            except Exception:  # noqa: BLE001 - group may not exist on this SKU
                pass
        privs = []
        for p in _STRIP_PRIVILEGES:
            try:
                privs.append([win32security.LookupPrivilegeValue(None, p), 0])
            except Exception:  # noqa: BLE001 - privilege absent on this SKU
                pass
        rtok = win32security.CreateRestrictedToken(
            tok, 0, deny_sids, privs, [])
        # Low integrity: S-1-16-4096 (SECURITY_MANDATORY_LOW_RID)
        low_sid = win32security.ConvertStringSidToSid("S-1-16-4096")
        win32security.SetTokenInformation(
            rtok, win32security.TokenIntegrityLevel, (low_sid, 0))
        return rtok
    finally:
        tok.Close()


def _make_job(mem_limit_mb: int):
    """Job object: kill-on-close + optional committed-memory cap."""
    import win32job

    job = win32job.CreateJobObject(None, "")
    limit_flags = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if mem_limit_mb > 0:
        limit_flags |= win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
    info = {
        "BasicLimitInformation": {
            "PerProcessUserTimeLimit": 0,
            "PerJobUserTimeLimit": 0,
            "LimitFlags": limit_flags,
            "MinimumWorkingSetSize": 0,
            "MaximumWorkingSetSize": 0,
            "ActiveProcessLimit": 0,
            "Affinity": 0,
            "PriorityClass": 0,
            "SchedulingClass": 0,
        },
        "IoInfo": {
            "ReadOperationCount": 0,
            "WriteOperationCount": 0,
            "OtherOperationCount": 0,
            "ReadTransferCount": 0,
            "WriteTransferCount": 0,
            "OtherTransferCount": 0,
        },
        "ProcessMemoryLimit": mem_limit_mb * 1024 * 1024,
        "JobMemoryLimit": 0,
        "PeakProcessMemoryUsed": 0,
        "PeakJobMemoryUsed": 0,
    }
    win32job.SetInformationJobObject(
        job, win32job.JobObjectExtendedLimitInformation, info)
    return job


def hardened_launch(cmd: List[str], cwd: str, env: Dict[str, str],
                    mem_limit_mb: int = 512):
    """Create the process under the restricted token inside the job.

    Returns (process_handle, job_handle, out_read, err_read); raises on
    failure. Caller owns the handles and must close them.
    """
    import win32con
    import win32job
    import win32pipe
    import win32process
    import win32security

    sa = win32security.SECURITY_ATTRIBUTES()
    sa.bInheritHandle = 1
    out_read, out_write = win32pipe.CreatePipe(sa, 0)
    err_read, err_write = win32pipe.CreatePipe(sa, 0)

    rtok = _make_restricted_low_token()
    job = _make_job(mem_limit_mb)
    try:
        si = win32process.STARTUPINFO()
        si.dwFlags = win32con.STARTF_USESTDHANDLES
        si.hStdOutput = out_write
        si.hStdError = err_write
        si.hStdInput = 0
        hp, ht, _pid, _tid = win32process.CreateProcessAsUser(
            rtok, cmd[0], subprocess.list2cmdline(cmd), None, None, 1,
            (win32con.CREATE_SUSPENDED | win32con.CREATE_NO_WINDOW
             | win32con.CREATE_UNICODE_ENVIRONMENT),
            dict(env), cwd, si)
        # Child inherited the write ends; parent must drop them or reads hang.
        out_write.Close()
        err_write.Close()
        try:
            win32job.AssignProcessToJobObject(job, hp)
        except Exception:
            # Nested-job edge case (child of an already-jobbed process):
            # continue without the job rather than failing the launch.
            logger.warning("AssignProcessToJobObject failed; continuing "
                           "without job containment.")
        win32process.ResumeThread(ht)
        ht.Close()
        return hp, job, out_read, err_read
    finally:
        rtok.Close()


def _drain(handle, cap: int = 65536) -> str:
    """Read all available bytes from a pipe handle (child already exited)."""
    import win32file
    chunks = []
    total = 0
    try:
        while total < cap:
            hr, data = win32file.ReadFile(handle, min(8192, cap - total))
            if not data:
                break
            chunks.append(data)
            total += len(data)
    except Exception:  # noqa: BLE001 - broken pipe = end of stream
        pass
    finally:
        try:
            handle.Close()
        except Exception:  # noqa: BLE001
            pass
    return b"".join(chunks).decode("utf-8", errors="replace")


def hardened_wait(hp, job, out_read, err_read, timeout: float,
                  output_cap: int = 4000) -> dict:
    """Wait for a hardened process; kill via the job on timeout."""
    import win32event
    import win32job
    import win32process

    timed_out = False
    try:
        rc = win32event.WaitForSingleObject(hp, int(timeout * 1000))
        if rc == win32event.WAIT_TIMEOUT:
            timed_out = True
            try:
                win32job.TerminateJobObject(job, 1)
            except Exception:  # noqa: BLE001
                try:
                    win32process.TerminateProcess(hp, 1)
                except Exception:  # noqa: BLE001
                    pass
        exit_code = win32process.GetExitCodeProcess(hp)
    finally:
        try:
            job.Close()  # KILL_ON_JOB_CLOSE reaps any survivors
        except Exception:  # noqa: BLE001
            pass
        try:
            hp.Close()
        except Exception:  # noqa: BLE001
            pass
    stdout = _drain(out_read)
    stderr = _drain(err_read)
    return {
        "returncode": exit_code if not timed_out else -1,
        "stdout": stdout[-output_cap:],
        "stderr": (stderr[-output_cap:] if not timed_out
                   else f"Execution timed out after {timeout}s."),
        "timed_out": timed_out,
        "sandboxed": True,
        "hardened": True,
    }


def mark_low_integrity(path: str) -> bool:
    """Label a directory tree low-integrity so hardened code can write there
    (and only there). Uses icacls; returns False on failure."""
    if not _IS_WINDOWS:
        return False
    try:
        r = subprocess.run(
            ["icacls", path, "/setintegritylevel", "(OI)(CI)low"],
            capture_output=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


__all__ = ["can_harden", "hardened_launch", "hardened_wait",
           "mark_low_integrity"]

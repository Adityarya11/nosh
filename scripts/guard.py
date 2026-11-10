"""Runs a command under a Windows job object and kills it if free RAM, free disk or VRAM crosses a limit; prints peak memory.
Usage: python scripts/guard.py <command...>"""

import ctypes
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

MIN_FREE_RAM_MB, MIN_FREE_DISK_GB, MAX_VRAM_PCT, JOB_LIMIT_MB, POLL_S = (
    1500,
    2,
    90,
    3500,
    0.5,
)
k32 = ctypes.windll.kernel32


class MEM(ctypes.Structure):
    _fields_ = [
        ("l", ctypes.c_ulong),
        ("load", ctypes.c_ulong),
        ("tot", ctypes.c_ulonglong),
        ("avail", ctypes.c_ulonglong),
        ("tp", ctypes.c_ulonglong),
        ("ap", ctypes.c_ulonglong),
        ("tv", ctypes.c_ulonglong),
        ("av", ctypes.c_ulonglong),
        ("ae", ctypes.c_ulonglong),
    ]


class BASIC(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("a", "b", "c", "d", "e", "f")]


class EXT(ctypes.Structure):
    _fields_ = [
        ("Basic", BASIC),
        ("Io", IO),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def free_ram_mb() -> int:
    m = MEM()
    m.l = ctypes.sizeof(MEM)
    k32.GlobalMemoryStatusEx(ctypes.byref(m))
    return int(m.avail / 2**20)


def vram_pct() -> float:
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        used, total = (float(x) for x in out.split(","))
        return 100 * used / total
    except Exception:
        return 0.0


def make_job() -> int:
    job = k32.CreateJobObjectW(None, None)
    info = EXT()
    info.Basic.LimitFlags = (
        0x200 | 0x2000
    )  # JOB_MEMORY limit + kill processes when the job closes
    info.JobMemoryLimit = JOB_LIMIT_MB * 2**20
    k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    return job


def peak_job_mb(job: int) -> float:
    info = EXT()
    k32.QueryInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info), None)
    return info.PeakJobMemoryUsed / 2**20


job = make_job()
base_free = free_ram_mb()
proc = subprocess.Popen(sys.argv[1:])
k32.AssignProcessToJobObject(job, ctypes.c_void_p(int(proc._handle)))
min_free, killed = base_free, None
while proc.poll() is None:
    ram, disk, vram = free_ram_mb(), shutil.disk_usage("E:/").free / 2**30, vram_pct()
    min_free = min(min_free, ram)
    killed = (
        f"free RAM {ram} MB"
        if ram < MIN_FREE_RAM_MB
        else f"free disk {disk:.1f} GB"
        if disk < MIN_FREE_DISK_GB
        else f"VRAM {vram:.0f}%"
        if vram > MAX_VRAM_PCT
        else None
    )
    if killed:
        k32.TerminateJobObject(job, 3)
        break
    time.sleep(POLL_S)
print(
    f"\nGUARD: peak job memory {peak_job_mb(job):.0f} MB (cap {JOB_LIMIT_MB} MB); system free RAM {base_free} -> min {min_free} MB"
    + (f"; KILLED ({killed})" if killed else "")
)
sys.exit(3 if killed else proc.returncode)

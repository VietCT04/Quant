"""Process-visible CPU and memory limits for bounded research execution."""

import os
from pathlib import Path

import psutil


def hardware():
    """Inspect process-visible CPUs, topology, available memory and native decoding threads."""
    import polars
    import pyarrow

    logical = os.cpu_count() or 1
    try:
        affinity = len(psutil.Process().cpu_affinity())
    except (AttributeError, NotImplementedError, OSError, psutil.Error):
        affinity = logical
    physical = min(affinity, psutil.cpu_count(logical=False) or affinity)
    quota = affinity
    memory = psutil.virtual_memory().available
    cgroup = Path("/sys/fs/cgroup")
    try:
        maximum, period = (cgroup / "cpu.max").read_text().split()
        if maximum != "max":
            quota = min(quota, max(1, -(-int(maximum) // int(period))))
    except (OSError, ValueError):
        pass
    try:
        maximum = (cgroup / "memory.max").read_text().strip()
        if maximum != "max":
            memory = max(
                0,
                min(
                    memory, int(maximum) - int((cgroup / "memory.current").read_text())
                ),
            )
    except (OSError, ValueError):
        pass
    return dict(
        logical=logical,
        affinity=affinity,
        physical=physical,
        usable_cpus=quota,
        available_bytes=memory,
        arrow_threads=pyarrow.cpu_count(),
        polars_threads=polars.thread_pool_size(),
        python_gil=getattr(__import__("sys"), "_is_gil_enabled", lambda: True)(),
    )


def worker_count(workers, *, cpu_limit=None, tasks=None):
    profile = hardware()
    cap = (
        profile["usable_cpus"]
        if cpu_limit is None
        else min(cpu_limit, profile["usable_cpus"])
    )
    if type(cap) is not int or cap < 1:
        raise ValueError("CPU limit must be a positive integer")
    count = min(profile["physical"], cap) if workers == "auto" else workers
    if type(count) is not int or not 1 <= count <= cap:
        raise ValueError(f"Worker count must be between 1 and {cap}")
    return count if tasks is None else min(count, max(1, tasks))

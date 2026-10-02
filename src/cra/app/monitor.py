"""What the admin console's Server info tab shows about the machine and process.

Read from ``/proc`` and the cgroup files rather than through a package: a few
numbers do not earn a dependency. Off Linux (a developer's laptop) the host
figures are missing and the page says so instead of guessing.

A container sees the host's memory in ``/proc/meminfo``, so the cgroup's own
usage and limit are reported next to it: the limit is what kills the process.
"""

import os
import platform
import resource
import shutil
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROC = Path("/proc")
CGROUP = Path("/sys/fs/cgroup")
# the credit left changes with every answer, but asking OpenRouter on every
# poll of every open console would be pointless traffic
CREDIT_TTL_S = 60.0
# cgroup v1 spells "no limit" as a number near 2**63
NO_LIMIT = 2**60


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def host_memory(proc: Path = PROC) -> dict[str, int] | None:
    """``/proc/meminfo`` in bytes; None where there is no such file."""
    info: dict[str, int] = {}
    for line in (_read(proc / "meminfo") or "").splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            info[name] = int(parts[0]) * (1024 if parts[1:] == ["kB"] else 1)
    if "MemTotal" not in info:
        return None
    return {
        "total": info["MemTotal"],
        "available": info.get("MemAvailable", info.get("MemFree", 0)),
        "swap_total": info.get("SwapTotal", 0),
        "swap_free": info.get("SwapFree", 0),
    }


def container_memory(cgroup: Path = CGROUP) -> dict[str, int | None] | None:
    """The cgroup's usage and limit; ``limit`` is None when there is none."""
    for current, limit in (
        ("memory.current", "memory.max"),
        ("memory/memory.usage_in_bytes", "memory/memory.limit_in_bytes"),
    ):
        used = (_read(cgroup / current) or "").strip()
        if not used.isdigit():
            continue
        cap = (_read(cgroup / limit) or "").strip()
        bounded = cap.isdigit() and int(cap) < NO_LIMIT
        return {"used": int(used), "limit": int(cap) if bounded else None}
    return None


def process_memory(proc: Path = PROC) -> dict[str, int]:
    """Resident memory now (Linux only), and the peak, which every platform has."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # ru_maxrss is in KiB on Linux and in bytes on macOS
    out = {"peak_rss": usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)}
    for line in (_read(proc / "self" / "status") or "").splitlines():
        if line.startswith("VmRSS:"):
            out["rss"] = int(line.split()[1]) * 1024
    return out


def disk(name: str, path: Path) -> dict[str, Any] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {"name": name, "path": str(path), "total": usage.total, "free": usage.free}


@dataclass
class Monitor:
    """Per process: when it started, the CPU time at the last poll, and the
    last answer about the model credit. Held by the app context, never at
    module level."""

    clock: Callable[[], float] = time.monotonic
    started: float = field(default_factory=time.time)
    _cpu_sample: tuple[float, float] | None = None
    _credit: tuple[float, dict[str, Any] | None] | None = None

    def cpu_percent(self) -> float | None:
        """This process's CPU use since the previous call, in percent of one
        core; None on the first call, which has nothing to compare with."""
        now = self.clock()
        usage = resource.getrusage(resource.RUSAGE_SELF)
        cpu = usage.ru_utime + usage.ru_stime
        previous, self._cpu_sample = self._cpu_sample, (now, cpu)
        if previous is None or now <= previous[0]:
            return None
        return round(100 * (cpu - previous[1]) / (now - previous[0]), 1)

    def cached_credit(self) -> tuple[bool, dict[str, Any] | None]:
        """Whether a fresh answer is cached, and the answer."""
        if self._credit is None or self.clock() - self._credit[0] > CREDIT_TTL_S:
            return False, None
        return True, self._credit[1]

    def remember_credit(self, credit: dict[str, Any] | None) -> None:
        self._credit = (self.clock(), credit)

    def system(self, paths: dict[str, Path]) -> dict[str, Any]:
        try:
            load: list[float] | None = list(os.getloadavg())
        except OSError:
            load = None
        return {
            "hostname": socket.gethostname(),
            "platform": platform.platform(terse=True),
            "python": platform.python_version(),
            "cpus": os.cpu_count(),
            "load": load,
            "uptime_s": round(time.time() - self.started),
            "process_cpu_percent": self.cpu_percent(),
            "process": process_memory(),
            "container": container_memory(),
            "host": host_memory(),
            "disks": [
                found
                for name, path in paths.items()
                if (found := disk(name, path)) is not None
            ],
        }

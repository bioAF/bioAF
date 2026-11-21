"""Which backend container and process is running infrastructure work.

Recorded on each infrastructure operation so startup recovery can tell what happened to it:

- the same container ID with a different container start proves the container restarted, and a
  container restart ends every process inside it, Terraform included;
- a different container ID, or a container start that cannot be read, proves nothing about the old
  container, so the operation needs an administrator's recovery;
- the same container start means only the server process restarted (a reload), which does not
  prove a Terraform child exited.
"""

from __future__ import annotations

import os
import socket


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="ascii") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _container_start() -> str | None:
    """The container's start instant: PID 1's start time (clock ticks since boot) plus the boot ID."""
    stat = _read("/proc/1/stat")
    boot_id = _read("/proc/sys/kernel/random/boot_id")
    if not stat or not boot_id:
        return None
    # Fields after the parenthesised command name; starttime is field 22 overall.
    fields = stat.rsplit(")", 1)[-1].split()
    if len(fields) < 20:
        return None
    return f"{boot_id}:{fields[19]}"


def current_runner() -> dict:
    return {
        "container_id": socket.gethostname(),
        "container_start": _container_start(),
        "pid": os.getpid(),
    }


def container_restarted(recorded: dict | None, now: dict) -> bool:
    """True only when the recorded container is this one and it has restarted since."""
    if not recorded:
        return False
    before = recorded.get("container_start")
    after = now.get("container_start")
    return bool(before and after and recorded.get("container_id") == now.get("container_id") and before != after)


def same_container(recorded: dict | None, now: dict) -> bool:
    if not recorded:
        return False
    return recorded.get("container_id") == now.get("container_id") and bool(
        recorded.get("container_start") and recorded.get("container_start") == now.get("container_start")
    )


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

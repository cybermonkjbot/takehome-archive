"""Collector job that gathers system metrics and persists them to PostgreSQL and local cache."""

from __future__ import annotations

import datetime
import json
import logging
import math
import os
import platform
import signal
import socket
import sys
import uuid
from pathlib import Path
from types import FrameType
from typing import Any, Dict

# Configure structured logging for Kubernetes stdout
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("collector")


def terminate(signum: int, _frame: FrameType | None) -> None:
    """Exit cleanly on cancellation signal, forwarding standard exit code."""
    sig_name = signal.Signals(signum).name
    logger.info(json.dumps({
        "level": "INFO",
        "message": f"Received {sig_name}; terminating collector",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }))
    sys.exit(128 + signum)


def read_cgroup_file(path: str) -> str | None:
    """Safely read a cgroup pseudo-file."""
    try:
        p = Path(path)
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return None


def get_cpu_quota_millicores() -> int | None:
    """Determine CPU quota in millicores from cgroups v2 or v1."""
    # cgroups v2: /sys/fs/cgroup/cpu.max contains "<max|quota> <period>"
    val = read_cgroup_file("/sys/fs/cgroup/cpu.max")
    if val:
        parts = val.split()
        if len(parts) >= 2 and parts[0] != "max":
            try:
                quota, period = int(parts[0]), int(parts[1])
                if period > 0:
                    return max(1, math.ceil((quota / period) * 1000))
            except ValueError:
                pass

    # cgroups v1: /sys/fs/cgroup/cpu/cpu.cfs_quota_us and cpu.cfs_period_us
    quota_val = read_cgroup_file("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    period_val = read_cgroup_file("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if quota_val and period_val:
        try:
            quota = int(quota_val)
            period = int(period_val)
            if quota > 0 and period > 0:
                return max(1, math.ceil((quota / period) * 1000))
        except ValueError:
            pass

    return None


def get_memory_limit_bytes() -> int | None:
    """Determine memory limit in bytes from cgroups v2 or v1."""
    # cgroups v2: /sys/fs/cgroup/memory.max
    val = read_cgroup_file("/sys/fs/cgroup/memory.max")
    if val and val != "max":
        try:
            return int(val)
        except ValueError:
            pass

    # cgroups v1: /sys/fs/cgroup/memory/memory.limit_in_bytes
    val1 = read_cgroup_file("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if val1:
        try:
            num = int(val1)
            if num < (1 << 60):
                return num
        except ValueError:
            pass

    return None


def get_memory_current_bytes() -> int | None:
    """Determine memory current usage in bytes."""
    # cgroups v2: /sys/fs/cgroup/memory.current
    val = read_cgroup_file("/sys/fs/cgroup/memory.current")
    if val:
        try:
            return int(val)
        except ValueError:
            pass

    # cgroups v1: /sys/fs/cgroup/memory/memory.usage_in_bytes
    val1 = read_cgroup_file("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    if val1:
        try:
            return int(val1)
        except ValueError:
            pass

    return None


def get_1m_load_avg() -> float | None:
    """Get 1-minute system load average."""
    if hasattr(os, "getloadavg"):
        try:
            return round(os.getloadavg()[0], 2)
        except OSError:
            pass
    return None


def gather_snapshot() -> Dict[str, Any]:
    """Gather real system and runtime telemetry matching public.system_snapshots."""
    run_id = os.environ.get("COLLECTOR_RUN_ID") or str(uuid.uuid4())
    sampled_at = datetime.datetime.now(datetime.timezone.utc)
    pod_name = os.environ.get("POD_NAME") or socket.gethostname() or "collector-local"

    return {
        "run_id": run_id,
        "sampled_at": sampled_at,
        "pod_name": pod_name,
        "kernel_release": platform.release(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "logical_cpu_count": os.cpu_count() or 1,
        "cpu_quota_millicores": get_cpu_quota_millicores(),
        "memory_limit_bytes": get_memory_limit_bytes(),
        "memory_current_bytes": get_memory_current_bytes(),
        "load_average_1m": get_1m_load_avg(),
    }


def persist_snapshot(snapshot: Dict[str, Any]) -> bool:
    """Write a local development cache and persist to PostgreSQL when configured."""
    cache_path = Path(os.environ.get("COLLECTOR_OUTPUT_PATH", "/tmp/collector-status.json"))
    tmp_path = cache_path.with_suffix(".tmp")
    serializable = dict(snapshot)
    serializable["sampled_at"] = snapshot["sampled_at"].isoformat()
    try:
        tmp_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
        tmp_path.replace(cache_path)
    except OSError as err:
        logger.warning(json.dumps({
            "level": "WARN",
            "message": f"Local cache write failed: {type(err).__name__}",
        }))

    database_required = os.environ.get("COLLECTOR_REQUIRE_DATABASE", "false").lower() == "true"
    if not os.environ.get("DATABASE_HOST"):
        if database_required:
            logger.error(json.dumps({
                "level": "ERROR",
                "message": "Database persistence is required but is not configured.",
            }))
            return False
        return True

    try:
        from datastore.postgres import connect

        with connect(application_name="snapshot-collector") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.system_snapshots (
                        run_id, sampled_at, pod_name, kernel_release, architecture,
                        python_version, logical_cpu_count, cpu_quota_millicores,
                        memory_limit_bytes, memory_current_bytes, load_average_1m
                    ) VALUES (
                        %(run_id)s, %(sampled_at)s, %(pod_name)s, %(kernel_release)s, %(architecture)s,
                        %(python_version)s, %(logical_cpu_count)s, %(cpu_quota_millicores)s,
                        %(memory_limit_bytes)s, %(memory_current_bytes)s, %(load_average_1m)s
                    ) ON CONFLICT (run_id) DO NOTHING
                    """,
                    snapshot,
                )
                cur.execute(
                    "DELETE FROM public.system_snapshots "
                    "WHERE sampled_at < NOW() - INTERVAL '30 days'"
                )
        logger.info(json.dumps({
            "level": "INFO",
            "message": "Persisted snapshot to PostgreSQL.",
            "run_id": snapshot["run_id"],
        }))
        return True
    except Exception as exc:
        # Driver text can include endpoint details; keep logs free of connection data.
        logger.error(json.dumps({
            "level": "ERROR",
            "message": f"PostgreSQL persistence failed: {type(exc).__name__}.",
            "run_id": snapshot["run_id"],
        }))
        return False


def main() -> None:
    """Main collector execution."""
    logger.info(json.dumps({
        "level": "INFO",
        "message": "Collector starting telemetry collection",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }))
    snapshot = gather_snapshot()
    if not persist_snapshot(snapshot):
        raise SystemExit(1)
    serializable = dict(snapshot)
    serializable["sampled_at"] = snapshot["sampled_at"].isoformat()
    logger.info(json.dumps({
        "level": "INFO",
        "message": "Collector completed collection cycle",
        "snapshot": serializable,
    }))


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    main()

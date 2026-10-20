"""PostgreSQL connection settings and snapshot queries."""

from __future__ import annotations

import os
from pathlib import Path

try:
    import psycopg
except ImportError:
    psycopg = None


def _secret(path_variable: str) -> str:
    path = os.environ.get(path_variable)
    if not path:
        raise RuntimeError(f"{path_variable} is not configured")
    value = Path(path).read_text(encoding="utf-8").rstrip("\r\n")
    if not value:
        raise RuntimeError(f"{path_variable} is empty")
    return value


def connect(*, application_name: str) -> psycopg.Connection:
    """Open a short-lived TLS connection using credentials mounted as files."""
    if psycopg is None:
        raise RuntimeError("psycopg is not installed")

    sslmode = os.environ.get("DATABASE_SSLMODE", "verify-full")
    if sslmode != "verify-full":
        raise RuntimeError("DATABASE_SSLMODE must be verify-full")

    host = os.environ.get("DATABASE_HOST", "")
    database = os.environ.get("DATABASE_NAME", "")
    try:
        port = int(os.environ.get("DATABASE_PORT", "5432"))
    except ValueError as error:
        raise RuntimeError("DATABASE_PORT must be an integer") from error
    if not host or not database or not 1 <= port <= 65535:
        raise RuntimeError("database endpoint configuration is incomplete")

    ca_file = os.environ.get("DATABASE_CA_FILE", "/var/run/secrets/datastore/ca.crt")
    return psycopg.connect(
        host=host,
        port=port,
        dbname=database,
        user=_secret("DATABASE_USERNAME_FILE"),
        password=_secret("DATABASE_PASSWORD_FILE"),
        sslmode=sslmode,
        sslrootcert=ca_file,
        connect_timeout=5,
        application_name=application_name,
        options="-c timezone=UTC -c statement_timeout=7000 -c lock_timeout=2000",
    )


SNAPSHOT_COLUMNS = (
    "run_id",
    "sampled_at",
    "pod_name",
    "kernel_release",
    "architecture",
    "python_version",
    "logical_cpu_count",
    "cpu_quota_millicores",
    "memory_limit_bytes",
    "memory_current_bytes",
    "load_average_1m",
)


def latest_snapshot() -> dict[str, object] | None:
    with connect(application_name="snapshot-api") as connection:
        with connection.cursor() as cursor:
            # SNAPSHOT_COLUMNS is an internal static tuple; no external user inputs
            cursor.execute(
                "SELECT " + ", ".join(SNAPSHOT_COLUMNS)  # nosec B608
                + " FROM public.system_snapshots ORDER BY sampled_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
    if row is None:
        return None
    snapshot = dict(zip(SNAPSHOT_COLUMNS, row, strict=True))
    sampled_at = snapshot["sampled_at"]
    if not hasattr(sampled_at, "isoformat"):
        raise RuntimeError("database returned an invalid snapshot timestamp")
    snapshot["sampled_at"] = sampled_at.isoformat().replace("+00:00", "Z")
    return snapshot


def check_schema() -> None:
    """Verify the API role can reach the provisioned snapshot table."""
    with connect(application_name="snapshot-api-health") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM public.system_snapshots LIMIT 1")
            cursor.fetchone()

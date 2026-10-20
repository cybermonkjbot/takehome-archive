"""Unified FastAPI service providing system telemetry snapshots and Kubernetes component logs."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hmac
import json
import logging
import os
from pathlib import Path
import re
import ssl
import sys
from typing import Any, AsyncIterator, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request as URLRequest, build_opener

# Ensure monorepo root is on sys.path for local and container execution
repo_root = str(Path(__file__).resolve().parent.parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

# If running directly under system python without virtual environment activated,
# transparently re-exec with the workspace virtual environment if available.
try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    venv_python = Path(repo_root) / ".venv" / "bin" / "python"
    if venv_python.is_file() and sys.executable != str(venv_python):
        os.execv(str(venv_python), [str(venv_python)] + sys.argv)
    raise

from fastapi import FastAPI, Path as FastPath, Query, Request as FastAPIRequest, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from datastore.postgres import check_schema, latest_snapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("api")

NAMESPACE = "demo"
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
API_TIMEOUT_SECONDS = 3
MAX_PODS = 6
MAX_POD_LIST_BYTES = 256 * 1024
MAX_LOG_BYTES = 16 * 1024
MAX_TAIL_LINES = 200

ComponentName = Literal["web-app", "collector", "datastore"]

COMPONENTS: dict[ComponentName, dict[str, str]] = {
    "web-app": {
        "selector": "app.kubernetes.io/name=web-app",
        "container": "web-app",
    },
    "collector": {
        "selector": "app.kubernetes.io/name=collector",
        "container": "collector",
    },
    "datastore": {
        "selector": "cnpg.io/cluster=postgres-cluster",
        "container": "postgres",
    },
}

STACK_COMPONENTS: tuple[tuple[str, str, str], ...] = (
    ("datastore", "PostgreSQL", "cnpg.io/cluster=postgres-cluster"),
    ("snapshot-api", "Snapshot API", "app.kubernetes.io/name=snapshot-api"),
    ("web-app", "Web dashboard", "app.kubernetes.io/name=web-app"),
)
LOG_TOKEN_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class LogsUnavailable(Exception):
    """Raised when a Kubernetes API request cannot be completed securely."""


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = Field(..., description="Service health indicator")


class SnapshotResponse(BaseModel):
    snapshot: dict[str, Any] | None = Field(default=None, description="Latest collected system metrics")


class PodLogEntry(BaseModel):
    name: str = Field(..., description="Pod name")
    container: str = Field(..., description="Container name within the pod")
    logs: str = Field(..., description="Retrieved log contents")
    error: bool = Field(default=False, description="Whether log retrieval encountered an error")


class ComponentLogsResponse(BaseModel):
    component: str = Field(..., description="Target component name")
    tail_lines: int = Field(..., description="Number of tail lines requested")
    pods: list[PodLogEntry] = Field(default_factory=list, description="Log entries per pod")
    truncated: bool = Field(default=False, description="Whether logs were truncated by byte/pod limits")
    has_error: bool = Field(default=False, description="Whether any pod log encountered an error")


class StackComponentStatus(BaseModel):
    id: str
    name: str
    state: Literal["ready", "degraded", "missing"]
    ready_pods: int
    observed_pods: int


class StackOverviewResponse(BaseModel):
    checked_at: str
    components: list[StackComponentStatus]


# ---------------------------------------------------------------------------
# Kubernetes Log Client
# ---------------------------------------------------------------------------

class KubernetesClient:
    """Read-only client scoped to in-cluster or loopback proxy Kubernetes endpoints."""

    def __init__(self) -> None:
        self.origin, self.use_service_account = self._resolve_origin()

    @staticmethod
    def _resolve_origin() -> tuple[str, bool]:
        configured = os.environ.get("KUBERNETES_API_URL")
        if configured:
            parsed = urlsplit(configured)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise LogsUnavailable("Invalid Kubernetes API endpoint configuration.")
            if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise LogsUnavailable("Unencrypted Kubernetes API access is strictly limited to loopback.")
            return f"{parsed.scheme}://{parsed.netloc}", parsed.scheme == "https"

        host = os.environ.get("KUBERNETES_SERVICE_HOST")
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        if not host or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise LogsUnavailable("Kubernetes API host or port is not configured.")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"https://{host}:{port}", True

    def request(self, path: str, max_bytes: int) -> bytes:
        headers = {"Accept": "application/json, text/plain"}
        context = None
        if self.use_service_account:
            try:
                with open(TOKEN_PATH, "r", encoding="utf-8") as token_file:
                    token = token_file.read(8192).strip()
                context = ssl.create_default_context(cafile=CA_PATH)
            except (OSError, ssl.SSLError):
                raise LogsUnavailable("Kubernetes service account credentials are unavailable.") from None
            if not token:
                raise LogsUnavailable("Kubernetes service account token is empty.")
            headers["Authorization"] = f"Bearer {token}"

        req = URLRequest(f"{self.origin}{path}", headers=headers, method="GET")
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
        try:
            with opener.open(req, timeout=API_TIMEOUT_SECONDS) as response:
                body = response.read(max_bytes + 1)
        except HTTPError:
            raise LogsUnavailable("Kubernetes request was denied or target resource is missing.") from None
        except (OSError, URLError, ssl.SSLError, TimeoutError):
            raise LogsUnavailable("Kubernetes API communication timed out or failed.") from None
        if len(body) > max_bytes:
            raise LogsUnavailable("Kubernetes response exceeded maximum buffer limit.")
        return body

    def fetch_pod_logs(self, pod_name: str, container: str, tail_lines: int) -> tuple[str, bool, bool]:
        query = urlencode({
            "container": container,
            "tailLines": str(tail_lines),
            "limitBytes": str(MAX_LOG_BYTES),
            "timestamps": "true",
        })
        path = f"/api/v1/namespaces/{NAMESPACE}/pods/{quote(pod_name, safe='')}/log?{query}"
        try:
            body = self.request(path, MAX_LOG_BYTES)
        except LogsUnavailable:
            return "Log unavailable.", False, True
        return body.decode("utf-8", errors="replace"), len(body) >= MAX_LOG_BYTES, False

    def get_component_logs(self, component: ComponentName, tail_lines: int) -> ComponentLogsResponse:
        config = COMPONENTS[component]
        query = urlencode({"labelSelector": config["selector"], "limit": "100"})
        path = f"/api/v1/namespaces/{NAMESPACE}/pods?{query}"
        try:
            payload = self.request(path, MAX_POD_LIST_BYTES)
            items = json.loads(payload).get("items", [])
        except (LogsUnavailable, ValueError, AttributeError, TypeError):
            raise LogsUnavailable("Unable to list pods for component.") from None

        if not isinstance(items, list):
            raise LogsUnavailable("Invalid pod list response from Kubernetes API.")

        pods = [
            pod for pod in items
            if isinstance(pod, dict)
            and isinstance(pod.get("metadata"), dict)
            and isinstance(pod["metadata"].get("name"), str)
        ]
        pods.sort(key=lambda p: p["metadata"].get("creationTimestamp", ""), reverse=True)
        truncated = len(pods) > MAX_PODS
        selected = pods[:MAX_PODS]

        entries: list[PodLogEntry] = []
        has_error = False
        for pod in selected:
            pod_name = pod["metadata"]["name"]
            logs, log_truncated, log_err = self.fetch_pod_logs(pod_name, config["container"], tail_lines)
            if log_truncated:
                truncated = True
            if log_err:
                has_error = True
            entries.append(PodLogEntry(
                name=pod_name,
                container=config["container"],
                logs=logs,
                error=log_err,
            ))

        return ComponentLogsResponse(
            component=component,
            tail_lines=tail_lines,
            pods=entries,
            truncated=truncated,
            has_error=has_error,
        )

    def get_stack_overview(self) -> StackOverviewResponse:
        """Return live ready/observed pod counts for fixed stack selectors."""
        components: list[StackComponentStatus] = []
        for component_id, name, selector in STACK_COMPONENTS:
            query = urlencode({"labelSelector": selector, "limit": "100"})
            path = f"/api/v1/namespaces/{NAMESPACE}/pods?{query}"
            try:
                payload = json.loads(self.request(path, MAX_POD_LIST_BYTES))
            except (LogsUnavailable, ValueError, TypeError):
                raise LogsUnavailable("Unable to inspect current component status.") from None

            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise LogsUnavailable("Invalid Kubernetes pod status response.")

            ready_pods = 0
            observed_pods = 0
            for pod in items:
                if not isinstance(pod, dict):
                    continue
                metadata = pod.get("metadata")
                pod_status = pod.get("status")
                if not isinstance(metadata, dict) or not isinstance(pod_status, dict):
                    continue
                if metadata.get("deletionTimestamp"):
                    continue
                observed_pods += 1
                conditions = pod_status.get("conditions", [])
                is_ready = (
                    pod_status.get("phase") == "Running"
                    and isinstance(conditions, list)
                    and any(
                        isinstance(condition, dict)
                        and condition.get("type") == "Ready"
                        and condition.get("status") == "True"
                        for condition in conditions
                    )
                )
                ready_pods += int(is_ready)

            state = (
                "missing" if observed_pods == 0
                else "ready" if ready_pods == observed_pods
                else "degraded"
            )
            components.append(StackComponentStatus(
                id=component_id,
                name=name,
                state=state,
                ready_pods=ready_pods,
                observed_pods=observed_pods,
            ))

        return StackOverviewResponse(
            checked_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            components=components,
        )


def _configured_log_access_token() -> str:
    """Read and validate the high-entropy shared token mounted as a Secret file."""
    token_path = os.environ.get("LOG_API_TOKEN_FILE")
    if not token_path:
        raise LogsUnavailable("Log access is not configured.")
    try:
        token = Path(token_path).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        raise LogsUnavailable("Log access is not configured.") from None
    if not LOG_TOKEN_PATTERN.fullmatch(token):
        raise LogsUnavailable("Log access configuration is invalid.")
    return token


def _has_log_access(authorization: str | None, expected_token: str) -> bool:
    if not authorization or len(authorization) > 128:
        return False
    scheme, separator, presented = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not LOG_TOKEN_PATTERN.fullmatch(presented):
        return False
    return hmac.compare_digest(expected_token, presented)


# ---------------------------------------------------------------------------
# FastAPI Application & Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("Initializing API services...")
    yield
    logger.info("API services shutting down cleanly.")


app = FastAPI(
    title="Local Kubernetes Snapshot & Diagnostics API",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Any, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "invalid_parameters", "detail": str(exc)},
    )


@app.get("/healthz", response_model=HealthResponse, summary="Process liveness probe")
async def healthz() -> HealthResponse:
    """Local process health check; verifies web server liveness without external dependencies."""
    return HealthResponse(status="ok")


@app.get("/readyz", response_model=HealthResponse, summary="Workload readiness probe")
async def readyz() -> JSONResponse:
    """Readiness probe; confirms database schema connectivity and migration readiness."""
    try:
        await asyncio.to_thread(check_schema)
    except Exception as exc:
        logger.warning("Readiness probe check failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "error": "database_not_ready"},
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ready"})


@app.get("/api/v1/snapshot", response_model=SnapshotResponse, summary="Latest telemetry snapshot")
async def get_snapshot() -> Any:
    """Retrieves the newest system telemetry snapshot persisted by the collector CronJob."""
    try:
        snapshot = await asyncio.to_thread(latest_snapshot)
    except Exception as exc:
        logger.error("Failed to query datastore for snapshot: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "snapshot_unavailable", "detail": "snapshot_unavailable"},
        )
    return SnapshotResponse(snapshot=snapshot)


@app.get("/api/v1/overview", response_model=StackOverviewResponse, summary="Live stack overview")
async def get_stack_overview() -> Any:
    """Report observed pod readiness for the dashboard's fixed components."""
    try:
        client = KubernetesClient()
        overview = await asyncio.to_thread(client.get_stack_overview)
    except LogsUnavailable:
        logger.warning("Unable to read current stack status from the Kubernetes API.")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "overview_unavailable"},
            headers=NO_STORE_HEADERS,
        )
    return JSONResponse(content=overview.model_dump(), headers=NO_STORE_HEADERS)


@app.get("/api/v1/logs/{component}", response_model=ComponentLogsResponse, summary="Component pod logs")
async def get_component_logs(
    request: FastAPIRequest,
    component: ComponentName = FastPath(..., description="Target component: web-app, collector, or datastore"),
    tail: int = Query(default=100, ge=1, le=MAX_TAIL_LINES, description="Max lines to retrieve from log tail"),
) -> Any:
    """Streams tail logs for all pods backing the specified component in the demo namespace."""
    try:
        expected_token = _configured_log_access_token()
    except LogsUnavailable:
        logger.error("Log access is unavailable because its credential is not configured.")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "log_authentication_unavailable"},
            headers=NO_STORE_HEADERS,
        )
    if not _has_log_access(request.headers.get("authorization"), expected_token):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "invalid_log_access_secret"},
            headers={**NO_STORE_HEADERS, "WWW-Authenticate": 'Bearer realm="component-logs"'},
        )

    try:
        client = KubernetesClient()
        result = await asyncio.to_thread(client.get_component_logs, component, tail)
        return JSONResponse(content=result.model_dump(), headers=NO_STORE_HEADERS)
    except LogsUnavailable as exc:
        logger.warning("Log request unavailable for component %s: %s", component, exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "logs_unavailable", "detail": "logs_unavailable"},
            headers=NO_STORE_HEADERS,
        )
    except Exception as exc:
        logger.error("Unexpected error retrieving logs for component %s: %s", component, exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": "logs_unavailable", "detail": "logs_unavailable"},
            headers=NO_STORE_HEADERS,
        )


def main() -> None:
    import uvicorn

    # Binding to 0.0.0.0 is required inside the container network namespace
    host = os.environ.get("API_HOST", "0.0.0.0")  # nosec B104
    try:
        port = int(os.environ.get("PORT", "8080"))
    except ValueError:
        print("PORT must be an integer.", file=sys.stderr)
        raise SystemExit(2) from None

    if not 1 <= port <= 65535:
        print("PORT must be between 1 and 65535.", file=sys.stderr)
        raise SystemExit(2)

    logger.info("Starting Unified FastAPI service on %s:%d", host, port)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()

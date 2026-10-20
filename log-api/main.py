"""Read-only, bounded Kubernetes pod-log endpoint."""

from __future__ import annotations

import json
import os
import signal
import ssl
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import FrameType
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

NAMESPACE = "demo"
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
API_TIMEOUT_SECONDS = 3
MAX_PODS = 6
MAX_POD_LIST_BYTES = 256 * 1024
MAX_LOG_BYTES = 16 * 1024
MAX_TAIL_LINES = 200

COMPONENTS = {
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
POD_LOG_WORKERS = ThreadPoolExecutor(max_workers=MAX_PODS, thread_name_prefix="pod-log")


class LogsUnavailable(Exception):
    """A Kubernetes request could not be completed safely."""


def _api_origin() -> tuple[str, bool]:
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
            raise LogsUnavailable("Invalid Kubernetes API endpoint.")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise LogsUnavailable("Unencrypted Kubernetes API access is limited to loopback.")
        return f"{parsed.scheme}://{parsed.netloc}", parsed.scheme == "https"

    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    if not host or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise LogsUnavailable("Kubernetes API is not configured.")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"https://{host}:{port}", True


def _kubernetes_request(path: str, max_bytes: int) -> bytes:
    origin, use_service_account = _api_origin()
    headers = {"Accept": "application/json, text/plain"}
    context = None
    if use_service_account:
        try:
            with open(TOKEN_PATH, "r", encoding="utf-8") as token_file:
                token = token_file.read(8192).strip()
            context = ssl.create_default_context(cafile=CA_PATH)
        except (OSError, ssl.SSLError):
            raise LogsUnavailable("Kubernetes credentials are unavailable.") from None
        if not token:
            raise LogsUnavailable("Kubernetes credentials are unavailable.")
        headers["Authorization"] = f"Bearer {token}"

    request = Request(f"{origin}{path}", headers=headers, method="GET")
    # Do not let ambient proxy variables redirect a service-account bearer token.
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
    try:
        with opener.open(request, timeout=API_TIMEOUT_SECONDS) as response:
            body = response.read(max_bytes + 1)
    except HTTPError:
        # Keep API details, pod data, and credentials out of the browser response.
        raise LogsUnavailable("Kubernetes request was denied or unavailable.") from None
    except (OSError, URLError, ssl.SSLError, TimeoutError):
        raise LogsUnavailable("Kubernetes request was unavailable.") from None
    if len(body) > max_bytes:
        raise LogsUnavailable("Kubernetes response exceeded its size limit.")
    return body


def _pod_logs(name: str, container: str, tail_lines: int) -> tuple[str, bool, bool]:
    query = urlencode({
        "container": container,
        "tailLines": str(tail_lines),
        "limitBytes": str(MAX_LOG_BYTES),
        "timestamps": "true",
    })
    path = (
        f"/api/v1/namespaces/{NAMESPACE}/pods/{quote(name, safe='')}/log?{query}"
    )
    try:
        body = _kubernetes_request(path, MAX_LOG_BYTES)
    except LogsUnavailable:
        return "Log unavailable.", False, True
    return body.decode("utf-8", errors="replace"), len(body) >= MAX_LOG_BYTES, False


def component_logs(component: str, tail_lines: int) -> dict[str, object]:
    config = COMPONENTS.get(component)
    if config is None:
        raise KeyError(component)

    query = urlencode({"labelSelector": config["selector"], "limit": "100"})
    path = f"/api/v1/namespaces/{NAMESPACE}/pods?{query}"
    try:
        payload = _kubernetes_request(path, MAX_POD_LIST_BYTES)
        items = json.loads(payload).get("items", [])
    except (LogsUnavailable, ValueError, AttributeError, TypeError):
        raise LogsUnavailable("Logs are unavailable.") from None

    if not isinstance(items, list):
        raise LogsUnavailable("Logs are unavailable.")

    pods = [
        pod for pod in items
        if isinstance(pod, dict)
        and isinstance(pod.get("metadata"), dict)
        and isinstance(pod["metadata"].get("name"), str)
    ]
    pods.sort(
        key=lambda pod: pod["metadata"].get("creationTimestamp", ""),
        reverse=True,
    )
    truncated = len(pods) > MAX_PODS
    selected = pods[:MAX_PODS]

    def read_pod(pod: dict[str, object]) -> dict[str, object]:
        metadata = pod["metadata"]
        name = metadata["name"]
        logs, log_truncated, log_error = _pod_logs(name, config["container"], tail_lines)
        return {
            "name": name,
            "container": config["container"],
            "logs": logs,
            "truncated": log_truncated,
            "error": log_error,
        }

    results = list(POD_LOG_WORKERS.map(read_pod, selected))

    truncated = truncated or any(pod["truncated"] for pod in results)
    has_error = any(pod["error"] for pod in results)
    for pod in results:
        pod.pop("truncated")
    return {
        "component": component,
        "tail_lines": tail_lines,
        "pods": results,
        "truncated": truncated,
        "has_error": has_error,
    }


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    request_queue_size = 32

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]):
        self.worker_slots = threading.BoundedSemaphore(16)
        super().__init__(address, handler)

    def process_request(self, request, client_address) -> None:
        if not self.worker_slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.worker_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.worker_slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "ComponentLogs"
    sys_version = ""

    def send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/healthz":
            self.send_json(200, {"status": "ok"})
            return
        if path == "/readyz":
            try:
                query = urlencode({
                    "labelSelector": COMPONENTS["web-app"]["selector"],
                    "limit": "1",
                })
                _kubernetes_request(
                    f"/api/v1/namespaces/{NAMESPACE}/pods?{query}",
                    64 * 1024,
                )
            except LogsUnavailable:
                self.send_json(503, {"status": "not_ready"})
            else:
                self.send_json(200, {"status": "ready"})
            return

        parts = path.split("/")
        if len(parts) != 5 or parts[:4] != ["", "api", "v1", "logs"]:
            self.send_json(404, {"error": "not_found"})
            return
        component = parts[4]
        if component not in COMPONENTS:
            self.send_json(404, {"error": "not_found"})
            return

        query = urlsplit(self.path).query
        tail_value = "100"
        for pair in query.split("&"):
            key, separator, value = pair.partition("=")
            if key == "tail" and separator:
                tail_value = value
                break
        try:
            tail_lines = int(tail_value)
        except ValueError:
            self.send_json(400, {"error": "invalid_tail"})
            return
        if not 1 <= tail_lines <= MAX_TAIL_LINES:
            self.send_json(400, {"error": "invalid_tail"})
            return

        try:
            result = component_logs(component, tail_lines)
        except LogsUnavailable:
            self.send_json(503, {"error": "logs_unavailable"})
        except Exception:
            self.send_json(503, {"error": "logs_unavailable"})
        else:
            self.send_json(200, result)

    def log_message(self, format: str, *args: object) -> None:
        # Log only the fixed endpoint path and status, never query or log content.
        sys.stdout.write(f"{self.command} {urlsplit(self.path).path} {self._response_code()}\n")

    def _response_code(self) -> int:
        return getattr(self, "last_response_code", 0)

    def send_response(self, code: int, message: str | None = None) -> None:
        self.last_response_code = code
        super().send_response(code, message)


shutdown_event = threading.Event()


def request_shutdown(_signum: int, _frame: FrameType | None) -> None:
    shutdown_event.set()


def main() -> None:
    host = os.environ.get("LOG_API_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("PORT", "8181"))
    except ValueError:
        print("PORT must be an integer.", file=sys.stderr)
        raise SystemExit(2) from None
    if not 1 <= port <= 65535:
        print("PORT must be between 1 and 65535.", file=sys.stderr)
        raise SystemExit(2)

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    server = BoundedThreadingHTTPServer((host, port), Handler)
    server_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.5},
        name="log-api-http-server",
    )
    server_thread.start()
    print(f"Component logs API listening on {host}:{port}.")
    try:
        shutdown_event.wait()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()
        print("Component logs API drained and stopped.")


if __name__ == "__main__":
    main()

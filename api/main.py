"""Read-only HTTP API for the most recent persisted collector snapshot."""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from urllib.parse import urlsplit

# Ensure monorepo root is on sys.path for local execution
repo_root = str(Path(__file__).resolve().parent.parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from datastore.postgres import check_schema, latest_snapshot


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Bound worker threads so request spikes cannot create unbounded memory use."""

    daemon_threads = False
    block_on_close = True
    request_queue_size = 32

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]):
        self._worker_slots = threading.BoundedSemaphore(16)
        super().__init__(address, handler)

    def process_request(self, request, client_address) -> None:
        self._worker_slots.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "SnapshotAPI"
    sys_version = ""

    def send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
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
                check_schema()
            except Exception:
                self.send_json(503, {"status": "not_ready"})
            else:
                self.send_json(200, {"status": "ready"})
            return
        if path == "/api/v1/snapshot":
            try:
                snapshot = latest_snapshot()
            except Exception:
                self.send_json(503, {"error": "snapshot_unavailable"})
            else:
                self.send_json(200, {"snapshot": snapshot})
            return
        self.send_json(404, {"error": "not_found"})

    def log_message(self, format: str, *args: object) -> None:
        # Exclude query strings and user-supplied header values from access logs.
        path = urlsplit(self.path).path
        sys.stdout.write(
            f"{self.log_date_time_string()} {self.client_address[0]} "
            f"{self.command} {path} {self._response_code()}\n"
        )

    def _response_code(self) -> int:
        return getattr(self, "_last_response_code", 0)

    def send_response(self, code: int, message: str | None = None) -> None:
        self._last_response_code = code
        super().send_response(code, message)


def request_shutdown(_signum: int, _frame: FrameType | None) -> None:
    """Wake the main thread, which drains the server from outside serve_forever."""
    shutdown_event.set()


shutdown_event = threading.Event()


def main() -> None:
    # Binding to 0.0.0.0 is required inside the container network namespace
    host = os.environ.get("API_HOST", "0.0.0.0")  # nosec B104
    if not host:
        print("API_HOST must not be empty.", file=sys.stderr)
        raise SystemExit(2)
    try:
        port = int(os.environ.get("PORT", "8080"))
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
        name="http-server",
    )
    server_thread.start()
    print(f"Snapshot API listening on port {port}.")
    try:
        shutdown_event.wait()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()
        print("Snapshot API drained and stopped.")


if __name__ == "__main__":
    main()

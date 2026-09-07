"""A small local server.

Deliberately the standard library and nothing else. This is one page for one
person on one machine; a framework would be more to install, more to keep
current, and more surface for something that should never leave localhost.

It binds 127.0.0.1 only. There is no authentication because there is no
network path to it — if you ever change the bind address, that stops being
true.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import Config
from ..store import Store
from .api import metric_payload, today_payload

TEMPLATE = Path(__file__).with_name("index.html")
HOST = "127.0.0.1"


class Handler(BaseHTTPRequestHandler):
    config: Config

    # -- plumbing ----------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(status, json.dumps(payload, default=str).encode(),
                   "application/json; charset=utf-8")

    def log_message(self, *args: object) -> None:
        pass  # the terminal is for the user, not for request logs

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        route = urlparse(self.path)
        query = parse_qs(route.query)

        if route.path in ("/", "/index.html"):
            self._send(200, TEMPLATE.read_bytes(), "text/html; charset=utf-8")
            return

        if route.path == "/api/today":
            day = query.get("date", [None])[0]
            try:
                with self._store() as store:
                    self._json(today_payload(store, self.config,
                                             date.fromisoformat(day) if day else None))
            except Exception as exc:  # noqa: BLE001 - shown in the page
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            return

        if route.path == "/api/metric":
            metric = query.get("metric", [""])[0]
            days = int(query.get("days", ["90"])[0])
            try:
                with self._store() as store:
                    self._json(metric_payload(store, metric, days=days))
            except Exception as exc:  # noqa: BLE001 - shown in the page
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")

    def _store(self) -> Store:
        """Open for the length of one request and no longer.

        DuckDB gives a file to one writer or many readers, so holding a
        connection open here would block `health sync` in another terminal —
        which is exactly what you do while this page is up. When a sync does
        hold the file, say so in words rather than passing on a lock error.
        """
        if not self.config.db_path.exists():
            raise RuntimeError("No database yet — run `health init` first.")
        try:
            return Store(self.config.db_path, read_only=True)
        except Exception as exc:  # noqa: BLE001 - translated, not swallowed
            text = str(exc).lower()
            if "same database file" in text or "lock" in text or "being used" in text:
                raise RuntimeError(
                    "The database is busy — a sync is probably running. "
                    "Refresh in a moment."
                ) from exc
            raise


def serve(config: Config, port: int = 8899, open_browser: bool = True) -> None:
    handler = type("BoundHandler", (Handler,), {"config": config})
    server = ThreadingHTTPServer((HOST, port), handler)
    url = f"http://{HOST}:{port}/"
    print(f"health is at {url}  (ctrl-c to stop)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()

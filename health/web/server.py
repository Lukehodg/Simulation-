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
import tempfile
import threading
import webbrowser
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import Config
from ..store import Store
from .api import bloods_payload, metric_payload, today_payload

WEB = Path(__file__).parent
HOST = "127.0.0.1"
PAGES = {"/": "index.html", "/index.html": "index.html", "/bloods": "bloods.html"}
#: A lab report is a few hundred kilobytes; anything far past that is a mistake.
MAX_UPLOAD = 25 * 1024 * 1024
ALLOWED_UPLOADS = {".pdf", ".json", ".csv"}


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

        if route.path in PAGES:
            self._send(200, (WEB / PAGES[route.path]).read_bytes(),
                       "text/html; charset=utf-8")
            return

        if route.path == "/favicon.ico":
            # Browsers ask unprompted; answer rather than log a 404 at them.
            self._send(204, b"", "image/x-icon")
            return

        if route.path == "/style.css":
            self._send(200, (WEB / "style.css").read_bytes(),
                       "text/css; charset=utf-8")
            return

        if route.path == "/api/bloods":
            try:
                with self._store() as store:
                    self._json(bloods_payload(store, self.config))
            except Exception as exc:  # noqa: BLE001 - shown in the page
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
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

    # -- writes ------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        route = urlparse(self.path)

        if route.path == "/api/labs/upload":
            self._upload()
            return

        if route.path == "/api/labs/analyse":
            self._analyse()
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_UPLOAD:
            raise ValueError(f"file is larger than {MAX_UPLOAD // 1024 // 1024} MB")
        return self.rfile.read(length)

    def _upload(self) -> None:
        """The file arrives as the raw request body with its name in a header,
        which is all a single-user local page needs — no multipart parsing, and
        no temporary state to clean up beyond one file."""
        from ..sync import build_source

        try:
            name = Path(self.headers.get("X-Filename", "panel.pdf")).name
            suffix = Path(name).suffix.lower()
            if suffix not in ALLOWED_UPLOADS:
                raise ValueError(f"{suffix or 'that'} files are not a lab report — "
                                 f"upload a PDF, CSV or JSON")
            data = self._body()
            if not data:
                raise ValueError("the file was empty")

            with tempfile.TemporaryDirectory() as directory:
                incoming = Path(directory) / name
                incoming.write_bytes(data)
                source = build_source("labs", self.config)
                landed = source.add(incoming)
                records = source.parse(landed)
                with self._store(write=True) as store:
                    written = store.load(records)
                    store.record_raw(landed, "labs", "panel",
                                     datetime.now(timezone.utc), parsed=True)
                unknown = source.unknown_analytes(landed)

            self._json({"stored": written.get("lab_results", 0),
                        "unrecognised": unknown, "file": name})
        except Exception as exc:  # noqa: BLE001 - shown in the page
            self._json({"error": str(exc)}, 400)

    def _analyse(self) -> None:
        from ..analysis import analyse

        try:
            request = json.loads(self._body() or b"{}")
            with self._store() as store:
                result = analyse(store, self.config,
                                 question=request.get("question") or None,
                                 with_literature=request.get("literature", True))
            self._json({
                "text": result.text, "model": result.model,
                "sent": result.sent, "usage": result.usage,
                "literature_error": result.literature_error,
                "papers": [{"title": p.title, "design": p.design,
                            "journal": p.journal, "year": p.year, "url": p.url}
                           for p in result.papers],
            })
        except Exception as exc:  # noqa: BLE001 - shown in the page
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _store(self, write: bool = False) -> Store:
        """Open for the length of one request and no longer.

        DuckDB gives a file to one writer or many readers, so holding a
        connection open here would block `health sync` in another terminal —
        which is exactly what you do while this page is up. When a sync does
        hold the file, say so in words rather than passing on a lock error.
        """
        if not self.config.db_path.exists():
            raise RuntimeError("No database yet — run `health init` first.")
        try:
            return Store(self.config.db_path, read_only=not write)
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

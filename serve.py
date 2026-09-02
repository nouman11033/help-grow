#!/usr/bin/env python3
"""Serve the combat-stats site, with background MightPulse refresh."""

from __future__ import annotations

import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STATUS = DATA / "refresh-status.json"
_refresh_lock = threading.Lock()


def _status() -> dict:
    if STATUS.exists():
        try:
            return json.loads(STATUS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"running": False}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/status":
            self._json(200, _status())
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/refresh":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)

        if not _refresh_lock.acquire(blocking=False):
            self._json(409, {"ok": False, "error": "A refresh is already running.", **_status()})
            return

        def job():
            try:
                from refresh import load_api_key, main, write_status

                if not load_api_key():
                    write_status(running=False, error="KINGSHOT_API_KEY is not set.")
                    return
                main()
            except Exception as exc:
                try:
                    from refresh import write_status

                    write_status(running=False, error=str(exc))
                except Exception:
                    pass
            finally:
                _refresh_lock.release()

        threading.Thread(target=job, daemon=True).start()
        self._json(202, {"ok": True, "started": True, "message": "Refresh started. Player pages take several minutes."})

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))


def run(port: int = 8765) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Combat stats → http://127.0.0.1:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    run()

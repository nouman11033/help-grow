#!/usr/bin/env python3
"""Serve the combat-stats site, with MightPulse recall at POST /api/refresh."""

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


def _read_json(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


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
        body = _read_json(self)
        if path == "/api/kill":
            from refresh import kill_runtime
            try:
                kid = kill_runtime(body.get("kid"))
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            self._json(200, {"ok": True, "killed": True, "kid": kid})
            return
        if path != "/api/refresh":
            self.send_error(404)
            return

        if not _refresh_lock.acquire(blocking=False):
            self._json(409, {"ok": False, "error": "A recall is already running.", **_status()})
            return

        try:
            from refresh import RecallKilled, fast_refresh, load_api_key, slim_client_snapshot

            if not load_api_key():
                self._json(500, {"ok": False, "error": "KINGSHOT_API_KEY is not set."})
                return
            snapshot = fast_refresh(persist=True, kid=body.get("kid"))
            self._json(200, {
                "ok": True,
                "overlay": slim_client_snapshot(snapshot),
                "generated_at": snapshot.get("generated_at"),
                "kid": snapshot.get("kid"),
                "mode": "boards",
            })
        except RecallKilled as exc:
            self._json(200, {"ok": False, "killed": True, "error": str(exc)})
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._json(500, {"ok": False, "error": str(exc)})
        finally:
            _refresh_lock.release()

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

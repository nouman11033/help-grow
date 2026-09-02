"""Vercel POST /api/refresh — live boards + rosters (no 10-minute player crawl)."""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_refresh():
    path = ROOT / "refresh.py"
    spec = importlib.util.spec_from_file_location("ks_refresh", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            ks = _load_refresh()
            payload = {"ok": True, "has_key": bool(ks.load_api_key()), "mode": "boards"}
            self._json(200, payload)
        except Exception as exc:
            self._json(500, {"ok": False, "error": str(exc)})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        try:
            ks = _load_refresh()
            if not ks.load_api_key():
                self._json(500, {
                    "ok": False,
                    "error": "KINGSHOT_API_KEY is not set in Vercel environment variables.",
                })
                return
            snapshot = ks.fast_refresh(persist=False)
            overlay = ks.slim_client_snapshot(snapshot)
            self._json(200, {
                "ok": True,
                "overlay": overlay,
                "generated_at": snapshot.get("generated_at"),
                "mode": "boards",
            })
        except Exception as exc:
            traceback.print_exc()
            self._json(500, {"ok": False, "error": str(exc)})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

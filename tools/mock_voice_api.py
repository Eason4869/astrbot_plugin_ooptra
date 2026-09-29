"""本地模拟 Ooptra VOICE_API，便于在项目 1 落地前联调插件。

用法（标准库，无需额外依赖）：
  python mock_voice_api.py [port]

默认 3091。响应体与 Ooptra 契约一致。
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

JOINED = {"joined": True, "area": "demo-area", "channel": "demo-channel", "state": "playing"}

MEMBERS = {
    "count": 3,
    "members": [
        {"uid": "u1", "name": "小明", "mic": True, "speaker": True},
        {"uid": "u2", "name": "小红", "mic": False, "speaker": True},
        {"uid": "u3", "name": "小刚", "mic": True, "speaker": False},
    ],
}


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path in ("/health",):
            self._json(200, {"ok": True, "service": "mock-ooptra-voice-api"})
            return
        if path in ("/voice/status",):
            self._json(200, JOINED)
            return
        if path in ("/voice/members",):
            qs = parse_qs(parsed.query)
            area = (qs.get("area") or [""])[0]
            channel = (qs.get("channel") or [""])[0]
            self._json(200, {**MEMBERS, "area": area, "channel": channel})
            return
        self._json(404, {"ok": False, "error": f"not found: {path}"})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = {}
        if path == "/voice/join":
            area = str(body.get("area") or "")
            channel = str(body.get("channel") or "")
            if not area or not channel:
                self._json(400, {"ok": False, "error": "area/channel required"})
                return
            JOINED.update(joined=True, area=area, channel=channel, state="joined")
            self._json(200, {"ok": True, "joined": True, "area": area, "channel": channel})
            return
        if path == "/voice/leave":
            JOINED.update(joined=False, state="idle")
            self._json(200, {"ok": True, "joined": False})
            return
        self._json(404, {"ok": False, "error": f"not found: {path}"})

    def log_message(self, fmt: str, *args):  # noqa: A003
        print(f"[mock] {self.address_string()} {fmt % args}")


def main() -> None:
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3091
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock Ooptra VOICE_API on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

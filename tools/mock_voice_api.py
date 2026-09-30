"""本地模拟 Ooptra VOICE_API，便于在 Ooptra 侧就绪前联调插件。

用法（标准库，无需额外依赖）：
  python mock_voice_api.py [port]

默认 3090。响应体对齐 Ooptra ≥ 2.0.0 契约（含 GET /voice/channels）与方案切换接口。
本 mock 不校验 token。

数据**有意做成两个域 + 多个频道**：单域单频道是发现不了「群只绑了域没绑频道」
「跨域进房」这类问题的，而这两类恰恰是真实环境最容易踩的。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

_LOCK = threading.Lock()

DEFAULT_AREA = "demo-area"

# area -> {"channels": [{id, name}], "members": {channel_id: [member, ...]}}
AREAS: dict[str, dict[str, Any]] = {
    "demo-area": {
        "channels": [
            {"id": "demo-channel", "name": "开黑房"},
            {"id": "demo-channel-2", "name": "闲聊房"},
        ],
        "members": {
            "demo-channel": [
                {"uid": "u1", "name": "小明", "mic": True, "speaker": True},
                {"uid": "u2", "name": "小红", "mic": False, "speaker": True},
            ],
            "demo-channel-2": [
                {"uid": "u3", "name": "小刚", "m": 1, "hm": 0},
            ],
        },
    },
    "demo-area-2": {
        "channels": [{"id": "other-channel", "name": "二域房"}],
        "members": {
            "other-channel": [{"uid": "u9", "name": "路人", "m": 0, "hm": 0}],
        },
    },
}

JOINED: dict[str, Any] = {
    "backend": "gemini_live",
    "enabled": True,
    "joined": True,
    "area": DEFAULT_AREA,
    "channel": "demo-channel",
    "state": "playing",
    "default_area": DEFAULT_AREA,
    "default_channel": "demo-channel",
}


def _channel_counts(area: str) -> dict[str, int]:
    return {
        cid: len(rows) for cid, rows in (AREAS.get(area, {}).get("members") or {}).items()
    }


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _require_area(self, area: str) -> str | None:
        """域不存在时返回错误文本。

        用 400 而不是 404：404 在插件侧语义是「Ooptra 没这个接口」，
        容易把「域 ID 写错」误诊成「版本太旧」。
        """
        if not area:
            return "缺少 area 参数"
        if area not in AREAS:
            return f"未知 area：{area}（可用：{'、'.join(AREAS)}）"
        return None

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "mock-ooptra-voice-api",
                    "version": "2.0.0-mock",
                    "enabled": True,
                },
            )
            return

        if path == "/voice/status":
            with _LOCK:
                snapshot = dict(JOINED)
            self._json(200, {"ok": True, **snapshot})
            return

        if path == "/voice/channels":
            area = (qs.get("area") or [""])[0]
            bad = self._require_area(area)
            if bad:
                self._json(400, {"ok": False, "error": bad})
                return
            counts = _channel_counts(area)
            channels = [
                {"id": ch["id"], "name": ch["name"], "count": int(counts.get(ch["id"], 0))}
                for ch in AREAS[area]["channels"]
            ]
            channels.sort(key=lambda c: (-c["count"], c["name"]))
            with _LOCK:
                default_area = JOINED["default_area"]
                default_channel = JOINED["default_channel"]
            self._json(
                200,
                {
                    "ok": True,
                    "area": area,
                    "channels": channels,
                    "default_area": default_area,
                    "default_channel": default_channel,
                },
            )
            return

        if path == "/voice/members":
            area = (qs.get("area") or [DEFAULT_AREA])[0]
            channel = (qs.get("channel") or [""])[0]
            bad = self._require_area(area)
            if bad:
                self._json(400, {"ok": False, "error": bad})
                return
            grouped = AREAS[area]["members"]
            if channel:
                rows = [dict(m) for m in grouped.get(channel, [])]
            else:
                # 未指定频道时按域汇总，与 Ooptra 契约一致
                rows = [dict(m) for members in grouped.values() for m in members]
            self._json(
                200,
                {
                    "ok": True,
                    "count": len(rows),
                    "members": rows,
                    "area": area,
                    "channel": channel,
                    "channel_counts": _channel_counts(area),
                },
            )
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

        if path == "/api/config":
            updates = body.get("updates") if isinstance(body, dict) else None
            voice = updates.get("voice") if isinstance(updates, dict) else None
            backend = voice.get("backend") if isinstance(voice, dict) else None
            if backend not in ("gemini_live", "mimo_cascade"):
                self._json(400, {"ok": False, "error": "backend must be gemini_live or mimo_cascade"})
                return
            with _LOCK:
                changed = JOINED.get("backend") != backend
                JOINED["backend"] = backend
            self._json(200, {
                "ok": True,
                "changed": {"voice": ["backend"]},
                "hot_reloaded_fields": ["backend"] if changed else [],
                "restart_required": False,
                "notes": ["语音后端已切换"] if changed else [],
            })
            return

        if path == "/voice/join":
            area = str(body.get("area") or "")
            channel = str(body.get("channel") or "")
            if not area or not channel:
                self._json(400, {"ok": False, "error": "area/channel required"})
                return
            bad = self._require_area(area)
            if bad:
                self._json(400, {"ok": False, "error": bad})
                return
            # 跨域校验：频道必须属于该域，否则真实 Ooptra 也会失败
            known = {ch["id"] for ch in AREAS[area]["channels"]}
            if channel not in known:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": f"频道 {channel} 不属于域 {area}（该域：{'、'.join(sorted(known))}）",
                    },
                )
                return
            with _LOCK:
                JOINED.update(joined=True, area=area, channel=channel, state="joined")
            self._json(200, {"ok": True, "joined": True, "area": area, "channel": channel})
            return

        if path == "/voice/leave":
            with _LOCK:
                JOINED.update(joined=False, state="idle")
            self._json(200, {"ok": True, "joined": False})
            return

        self._json(404, {"ok": False, "error": f"not found: {path}"})

    def log_message(self, fmt: str, *args):  # noqa: A003
        print(f"[mock] {self.address_string()} {fmt % args}")


def main() -> None:
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3090
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock Ooptra VOICE_API on http://127.0.0.1:{port}")
    print(f"域：{'、'.join(AREAS)}（默认 {DEFAULT_AREA}）")
    server.serve_forever()


if __name__ == "__main__":
    main()

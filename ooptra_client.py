"""Ooptra VOICE_API 异步客户端。

API 契约（Ooptra 侧 VOICE_API）：
  GET  /voice/status
  GET  /voice/members?area=&channel=
  POST /voice/join   {"area": "...", "channel": "..."}
  POST /voice/leave  {}
  GET  /health
鉴权：Authorization: Bearer <token>（token 为空则不发送）。
"""

from __future__ import annotations

import re
from typing import Any

_ERROR_BODY_MAX = 160
_CJK_RE = re.compile(r"[一-鿿]")
_ID_SAFE_RE = re.compile(r"^[\w.:-]{1,128}$")


class OoptraError(Exception):
    """Ooptra API 调用失败。"""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class OoptraClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 8.0):
        self.base_url = (base_url or "").rstrip("/")
        self.token = (token or "").strip()
        self.timeout = timeout if timeout > 0 else 8.0

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        if not self.base_url:
            raise OoptraError("未配置 Ooptra VOICE_API 地址（api_base）")

        try:
            import httpx
        except ModuleNotFoundError as exc:
            raise OoptraError("缺少依赖 httpx，请先 pip install httpx") from exc

        url = f"{self.base_url}{path}"
        timeout = httpx.Timeout(self.timeout)
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=True) as client:
                resp = await client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=self._headers(),
                )
        except httpx.TimeoutException as exc:
            raise OoptraError(f"Ooptra API 超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise OoptraError(f"无法连接 Ooptra VOICE_API：{exc}") from exc

        if resp.status_code == 401:
            raise OoptraError("Ooptra API 鉴权失败，请检查 api_token", status_code=401)
        if resp.status_code == 404:
            raise OoptraError(
                "Ooptra 未提供该接口（请确认已启用 VOICE_API，或升级 Ooptra）",
                status_code=404,
            )
        if resp.status_code >= 400:
            detail = _safe_text(resp)
            raise OoptraError(
                f"Ooptra API 错误 HTTP {resp.status_code}：{detail}",
                status_code=resp.status_code,
            )

        if not resp.content:
            return {}
        try:
            data = resp.json()
        except ValueError as exc:
            raise OoptraError("Ooptra 返回的不是 JSON") from exc

        if isinstance(data, dict) and data.get("ok") is False:
            err = data.get("error") or data.get("message") or "unknown"
            raise OoptraError(_clip(str(err)))
        return data

    async def health(self) -> dict[str, Any]:
        try:
            data = await self._request("GET", "/health")
            return data if isinstance(data, dict) else {"ok": True}
        except OoptraError as exc:
            if exc.status_code == 401:
                raise
            return await self._request("GET", "/voice/status")

    async def status(self) -> dict[str, Any]:
        data = await self._request("GET", "/voice/status")
        return data if isinstance(data, dict) else {"raw": data}

    async def members(self, area: str, channel: str) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/voice/members",
            params={"area": area, "channel": channel},
        )
        return data if isinstance(data, dict) else {"raw": data}

    async def join(self, area: str, channel: str) -> dict[str, Any]:
        data = await self._request(
            "POST",
            "/voice/join",
            json_body={"area": area, "channel": channel},
        )
        return data if isinstance(data, dict) else {"ok": True, "raw": data}

    async def leave(self) -> dict[str, Any]:
        data = await self._request("POST", "/voice/leave", json_body={})
        return data if isinstance(data, dict) else {"ok": True, "raw": data}


def _clip(text: str, limit: int = _ERROR_BODY_MAX) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _safe_text(resp: Any) -> str:
    try:
        return _clip(str(resp.text))
    except Exception:
        return ""


def looks_like_label(text: str) -> bool:
    """是否更像备注文案（含中文），而非 Oopz ID。"""
    return bool(text) and bool(_CJK_RE.search(text))


def looks_like_id(text: str) -> bool:
    """宽松校验 Oopz 域/频道 ID 形态，避免把备注写进 ID 字段。"""
    return bool(text) and bool(_ID_SAFE_RE.match(text)) and not looks_like_label(text)


def resolve_group_mapping(group_map: Any, group_id: str | None) -> dict[str, str] | None:
    """从 group_map 解析当前 QQ 群绑定的 Oopz 目标。

    兼容两种值形态：
      {"area": "...", "channel": "...", "label": "..."}
      或简单字符串（视为 area:channel 或 area#channel）
    """
    if not group_id or not isinstance(group_map, dict):
        return None

    key = str(group_id).strip()
    raw = group_map.get(key)
    if raw is None:
        try:
            raw = group_map.get(int(key))
        except (TypeError, ValueError):
            raw = None
    if raw is None:
        return None

    if isinstance(raw, str):
        text = raw.strip()
        if ":" in text:
            area, channel = text.split(":", 1)
        elif "#" in text:
            area, channel = text.split("#", 1)
        else:
            area, channel = text, ""
        return {"area": area.strip(), "channel": channel.strip(), "label": ""}

    if isinstance(raw, dict):
        return {
            "area": str(raw.get("area") or "").strip(),
            "channel": str(raw.get("channel") or "").strip(),
            "label": str(raw.get("label") or "").strip(),
        }

    return None


def _is_muted_boolish(value: Any) -> bool | None:
    """mic/speaker 语义：False/0 表示关闭。None=未知。"""
    if value is None:
        return None
    if value is True:
        return False
    if value is False:
        return True
    if isinstance(value, (int, float)):
        return bool(value == 0)
    text = str(value).strip().lower()
    if text in {"muted", "mute", "off", "false", "0", "closed"}:
        return True
    if text in {"unmuted", "on", "true", "1", "open", "active"}:
        return False
    return None


def _is_muted_flag(value: Any) -> bool | None:
    """Oopz m/hm 标志：1=闭麦/闭听，0=正常。None=未知。"""
    if value is None:
        return None
    if value is True:
        return True
    if value is False:
        return False
    if isinstance(value, (int, float)):
        return bool(value == 1)
    text = str(value).strip().lower()
    if text in {"muted", "mute", "1", "off", "true"}:
        return True
    if text in {"unmuted", "0", "on", "false", "active"}:
        return False
    return None


def format_members(payload: dict[str, Any], *, label: str = "") -> str:
    """把 /voice/members 响应排成群消息文本。"""
    members = payload.get("members")
    if not isinstance(members, list):
        members = []

    listed = len(members)
    raw_count = payload.get("count")
    try:
        count = int(raw_count) if raw_count is not None else listed
    except (TypeError, ValueError):
        count = listed

    title = label or payload.get("channel") or payload.get("name") or "语音频道"
    if count != listed:
        lines_head = f"【{title}】在线 {count} 人（列表 {listed} 人）"
    else:
        lines_head = f"【{title}】在线 {count} 人"
    lines: list[str] = [lines_head]

    if not members:
        lines.append("（当前无人，或数据未就绪）")
        return "\n".join(lines)

    for i, m in enumerate(members, 1):
        if not isinstance(m, dict):
            lines.append(f"{i}. {m}")
            continue
        name = m.get("name") or m.get("user_name") or m.get("uid") or m.get("user_id") or "?"
        if "mic" in m:
            mic = _is_muted_boolish(m.get("mic"))
        else:
            mic = _is_muted_flag(m.get("m"))
        if "speaker" in m:
            speaker = _is_muted_boolish(m.get("speaker"))
        else:
            speaker = _is_muted_flag(m.get("hm"))
        flags = []
        if mic is True:
            flags.append("闭麦")
        if speaker is True:
            flags.append("闭听")
        suffix = f"（{', '.join(flags)}）" if flags else ""
        lines.append(f"{i}. {name}{suffix}")

    return "\n".join(lines)


def format_status(payload: dict[str, Any]) -> str:
    joined = bool(payload.get("joined"))
    if not joined:
        return "语音：未进房"
    area = payload.get("area") or "?"
    channel = payload.get("channel") or "?"
    state = payload.get("state") or payload.get("voice_state") or "-"
    return f"语音：已在房\n域/频道：{area} / {channel}\n状态：{state}"

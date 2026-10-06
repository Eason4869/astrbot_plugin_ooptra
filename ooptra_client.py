"""Ooptra VOICE_API 异步客户端。

API 契约（Ooptra ≥ 3.0.0 的 VOICE_API）：
  GET  /voice/status
  GET  /voice/members?area=&channel=
  GET  /voice/channels?area=          # 域内语音频道 + 在线人数
  POST /voice/join   {"area": "...", "channel": "..."}
  POST /voice/leave  {}
  POST /voice/auto-visit/config {"updates": {"areas": {"...": {"enabled": true}}}}
  POST /api/config  {"updates": {"voice": {"backend": "..."}}}  # 仅 WebUI 端口
  GET  /health
鉴权：Authorization: Bearer <token>（token 为空则不发送）。
token 与 Ooptra 侧保持一致：VOICE_API 默认挂在 WebUI 同端口，填 WEBUI_CONFIG.token；
若单独启用了 VOICE_API_CONFIG（独立端口），则填 VOICE_API_CONFIG.token。两边都留空则不校验。

两条硬约束：
  1. 请求**必须** trust_env=False（原因见 ``_request`` 内注释）。
  2. 契约不符要**报错**，不能伪造成成功或空数据——旧版把非 dict 响应包成
     {"raw": …}「假装成功」，而没有任何 formatter 读 raw，于是故障被显示成
     「当前没有人在语音频道」。
"""

from __future__ import annotations

import re
from typing import Any

_ERROR_BODY_MAX = 160
_CJK_RE = re.compile(r"[一-鿿]")
# 用 \Z 而非 $：$ 允许结尾换行，"abc\n" 会被误判成合法 ID
_ID_SAFE_RE = re.compile(r"^[\w.:-]{1,128}\Z")

# 只有这些状态码才值得从 /health 回退到 /voice/status（=服务不认识 /health）
_HEALTH_FALLBACK_STATUS = frozenset({404, 405, 501})

BACKEND_LABELS = {"gemini_live": "Gemini Live", "mimo_cascade": "MiMo 级联"}


def normalize_backend(name: str) -> str:
    """把用户输入收敛到支持的两种后端；未知方案不发给配置接口。"""
    compact = re.sub(r"[\s_-]+", "", name.lower())
    if compact in {"gemini", "geminilive"}:
        return "gemini_live"
    if compact in {"mimo", "mimocascade", "mimo级联"}:
        return "mimo_cascade"
    raise ValueError("用法：/语音方案 gemini 或 /语音方案 mimo；不带参数查询当前方案。")


class OoptraError(Exception):
    """Ooptra API 调用失败。"""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class OoptraClient:
    def __init__(
        self,
        base_url: str,
        token: str = "",
        timeout: float = 8.0,
        *,
        transport: Any = None,
    ):
        """``transport`` 仅供测试注入（httpx.MockTransport），生产保持 None。"""
        self.base_url = (base_url or "").rstrip("/")
        self.token = (token or "").strip()
        self.timeout = timeout if timeout > 0 else 8.0
        self._transport = transport

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
            # trust_env=False 是**必须**的：httpx 与 requests 不同，它**不会**自动
            # 绕过 localhost 代理。开着 trust_env，系统里的 HTTP_PROXY/HTTPS_PROXY
            # 会把 127.0.0.1 的请求也劫走——Ooptra 明明正常却报「无法连接」，
            # 而且 Bearer token 与请求体会被完整交给代理。
            async with httpx.AsyncClient(
                timeout=timeout,
                trust_env=False,
                transport=self._transport,
            ) as client:
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
            raise OoptraError(
                "Ooptra API 鉴权失败：api_token 需与 Ooptra 侧一致"
                "（默认填 WEBUI_CONFIG.token，独立 VOICE_API 则填 VOICE_API_CONFIG.token）",
                status_code=401,
            )
        if resp.status_code == 404:
            # 404 有三种完全不同的原因，必须让用户能分辨，否则只能瞎猜
            raise OoptraError(
                f"Ooptra 未提供接口 {method} {path}。"
                "可能原因：1) Ooptra 版本过旧（/voice/channels 需 ≥ 2.0.0）；"
                "2) api_base 端口/路径写错；"
                "3) 反向代理改写了路径",
                status_code=404,
            )
        if resp.status_code >= 400:
            detail = _safe_text(resp)
            raise OoptraError(
                f"Ooptra API 错误 HTTP {resp.status_code}：{detail}",
                status_code=resp.status_code,
            )

        # 空响应体不是「成功且无数据」：正常契约下每个端点都有 JSON body。
        # 旧版在这里 return {}，于是代理/异常服务返回 200 空体时被当成成功。
        if not resp.content:
            raise OoptraError(f"Ooptra 对 {method} {path} 返回了空响应体（服务可能异常或未就绪）")
        try:
            data = resp.json()
        except ValueError as exc:
            raise OoptraError(f"Ooptra 对 {method} {path} 返回的不是 JSON") from exc

        if isinstance(data, dict) and data.get("ok") is False:
            err = data.get("error") or data.get("message") or "unknown"
            raise OoptraError(_clip(str(err)))
        return data

    async def health(self) -> dict[str, Any]:
        """探活：优先 /health，仅当服务「不认识这个端点」时才回退 /voice/status。

        旧实现除 401 外一律回退，于是服务真挂掉/超时时要白等两个 timeout，
        而且 /语音自检 会把 status 载荷当成 health 打印。结果里带 ``via``
        标明数据实际来自哪个端点。
        """
        try:
            data = await self._request("GET", "/health")
            result = _expect_mapping(data, "/health")
            result["via"] = "/health"
            return result
        except OoptraError as exc:
            if exc.status_code not in _HEALTH_FALLBACK_STATUS:
                raise
        data = await self._request("GET", "/voice/status")
        result = _expect_mapping(data, "/voice/status")
        result["via"] = "/voice/status"
        return result

    async def status(self) -> dict[str, Any]:
        return _expect_mapping(await self._request("GET", "/voice/status"), "/voice/status")

    async def members(self, area: str, channel: str) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/voice/members",
            params={"area": area, "channel": channel},
        )
        return _expect_mapping(data, "/voice/members")

    async def channels(self, area: str) -> dict[str, Any]:
        """查询域内语音频道与在线人数。"""
        data = await self._request(
            "GET",
            "/voice/channels",
            params={"area": area},
        )
        return _expect_mapping(data, "/voice/channels")

    async def join(self, area: str, channel: str) -> dict[str, Any]:
        data = await self._request(
            "POST",
            "/voice/join",
            json_body={"area": area, "channel": channel},
        )
        # 旧版对非 dict 响应返回 {"ok": True, "raw": data}——**伪造进房成功**
        return _expect_mapping(data, "/voice/join")

    async def leave(self) -> dict[str, Any]:
        data = await self._request("POST", "/voice/leave", json_body={})
        return _expect_mapping(data, "/voice/leave")

    async def set_auto_visit(self, area: str, enabled: bool) -> dict[str, Any]:
        """仅更新指定域的串门开关，保留全局规则、域覆盖与其他域配置。"""
        if not isinstance(area, str) or not area.strip() or not isinstance(enabled, bool):
            raise ValueError("串门开关需要非空域 ID 与布尔值。")
        area = area.strip()
        path = "/voice/auto-visit/config"
        try:
            data = await self._request(
                "POST", path,
                json_body={"updates": {"areas": {area: {"enabled": enabled}}}},
            )
        except OoptraError as exc:
            if exc.status_code != 404:
                raise
            raise OoptraError(
                f"语音串门需要 Ooptra ≥ 3.0.0 的 {path} 接口。"
                "请升级 Ooptra，并检查 api_base 端口/路径及反向代理配置。",
                status_code=404,
            ) from exc
        result = _expect_mapping(data, path)
        changed = result.get("changed")
        fields = changed.get("auto_visit") if isinstance(changed, dict) else None
        if result.get("ok") is not True or not isinstance(fields, list) or "areas" not in fields:
            raise OoptraError("Ooptra 未确认保存串门开关，不能确认操作成功。")
        # 同时核对保存后的配置与控制器状态，避免仅落盘却未应用时误报成功。
        for key in ("config", "status"):
            section = result.get(key)
            areas = section.get("areas") if isinstance(section, dict) else None
            row = areas.get(area) if isinstance(areas, dict) else None
            if not isinstance(row, dict) or row.get("enabled") is not enabled:
                raise OoptraError("Ooptra 未确认目标域串门开关已保存并应用，请检查语音台与日志。")
        return result

    async def set_backend(self, backend: str) -> dict[str, Any]:
        """复用 Ooptra WebUI 的持久化/热重载接口，只修改语音后端。"""
        backend = normalize_backend(backend)
        try:
            data = await self._request(
                "POST", "/api/config",
                json_body={"updates": {"voice": {"backend": backend}}},
            )
        except OoptraError as exc:
            if exc.status_code != 404:
                raise
            raise OoptraError(
                "切换语音方案需要 Ooptra WebUI 的 /api/config 接口。"
                "请将 api_base 指向 WebUI（默认 http://127.0.0.1:3090），"
                "api_token 使用 WEBUI_CONFIG.token；独立 VOICE_API 端口不提供配置接口。"
                "若已使用 WebUI，请升级 Ooptra 并检查反向代理路径。",
                status_code=404,
            ) from exc
        result = _expect_mapping(data, "/api/config")
        changed = result.get("changed")
        voice_fields = changed.get("voice") if isinstance(changed, dict) else None
        if (
            result.get("ok") is not True
            or not isinstance(voice_fields, list)
            or "backend" not in voice_fields
        ):
            raise OoptraError("Ooptra 的 /api/config 未确认保存语音后端，不能确认方案切换。")
        return result


def _clip(text: str, limit: int = _ERROR_BODY_MAX) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _safe_text(resp: Any) -> str:
    try:
        return _clip(str(resp.text))
    except Exception:
        return ""


def _expect_mapping(data: Any, what: str) -> dict[str, Any]:
    """契约要求顶层是 JSON 对象；不是就报错。

    以前这里把非 dict 包成 {"raw": data} 假装成功，而没有任何 formatter 读 raw，
    结果契约不符被显示成「当前没有人在语音频道」——把故障伪装成空数据。
    """
    if isinstance(data, dict):
        return data
    raise OoptraError(
        f"Ooptra 的 {what} 不符合契约：期望 JSON 对象，实际是 {type(data).__name__}"
    )


def _contract_warn(what: str) -> str:
    """格式化时遇到契约不符的统一提示（比「没人在语音」这种误报有用）。"""
    return f"{what}：Ooptra 返回结构不符合契约（需 Ooptra ≥ 2.0.0），可用 /语音自检 定位"


def looks_like_label(text: str) -> bool:
    """是否更像备注文案（含中文），而非 Oopz ID。"""
    return bool(text) and bool(_CJK_RE.search(text))


def looks_like_id(text: str) -> bool:
    """宽松校验 Oopz 域/频道 ID 形态，避免把备注写进 ID 字段。"""
    return bool(text) and bool(_ID_SAFE_RE.match(text)) and not looks_like_label(text)


def _as_area_list(value: Any) -> list[str]:
    """把 area 字段归一成 ID 列表：支持单值、列表、逗号/顿号分隔字符串。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    if "," in text or "、" in text:
        parts = re.split(r"[,、]", text)
        return [p.strip() for p in parts if p.strip()]
    return [text]


def resolve_group_mapping(
    group_map: Any,
    group_id: str | None,
    *,
    preferred_area: str = "",
) -> dict[str, Any] | None:
    """从 group_map 解析当前 QQ 群绑定的 Oopz 目标。

    兼容值形态：
      {"area": "...", "channel": "...", "label": "..."}
      {"areas": ["id1", "id2"], "channel": "..."}   # 一群多域
      或简单字符串（视为 area:channel 或 area#channel）

    一群绑了多个域时，优先取 preferred_area（通常是 Ooptra 的默认域），否则取第一个。
    返回值里 area 是选中的那个，areas 是全部候选。
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
        areas = _as_area_list(area.strip())
        return _pick_area(areas, channel.strip(), "", preferred_area)

    if isinstance(raw, dict):
        areas = _as_area_list(raw.get("areas") if raw.get("areas") is not None else raw.get("area"))
        return _pick_area(
            areas,
            str(raw.get("channel") or "").strip(),
            str(raw.get("label") or "").strip(),
            preferred_area,
        )

    return None


def _pick_area(
    areas: list[str],
    channel: str,
    label: str,
    preferred_area: str,
) -> dict[str, Any] | None:
    if not areas:
        return {"area": "", "areas": [], "channel": channel, "label": label}
    chosen = areas[0]
    pref = (preferred_area or "").strip()
    if pref and pref in areas:
        chosen = pref
    return {"area": chosen, "areas": areas, "channel": channel, "label": label}


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
    if not isinstance(payload, dict):
        return _contract_warn("语音成员")
    members = payload.get("members")
    if not isinstance(members, list):
        # 注意区分「列表为空」（真的没人）与「没有 members 字段」（契约不符）
        return _contract_warn("语音成员")

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
    if not isinstance(payload, dict) or "joined" not in payload:
        return _contract_warn("语音状态")
    joined = bool(payload.get("joined"))
    if not joined:
        return "语音：未进房"
    area = payload.get("area") or "?"
    channel = payload.get("channel") or "?"
    state = payload.get("state") or payload.get("voice_state") or "-"
    return f"语音：已在房\n域/频道：{area} / {channel}\n状态：{state}"


def channel_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """把 /voice/channels 响应归一成 [{id, name, count}]。"""
    if not isinstance(payload, dict):
        return []
    rows = payload.get("channels")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            count = int(row.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        out.append(
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or row.get("id") or ""),
                "count": count,
            }
        )
    return out


def format_channel_counts(payload: dict[str, Any], *, label: str = "") -> str:
    """把 /voice/channels 排成「有人的频道 + 人数」群消息文本。"""
    rows = channel_rows(payload)
    if not rows and not isinstance(payload.get("channels"), list):
        return _contract_warn("语音频道列表")
    occupied = [r for r in rows if r["count"] > 0]
    title = label or "有人的语音频道"
    lines = [f"【{title}】"]
    if not occupied:
        lines.append("当前没有人在语音频道。")
        if rows:
            lines.append(f"（该域共 {len(rows)} 个语音频道）")
        return "\n".join(lines)

    total = sum(r["count"] for r in occupied)
    lines.append(f"共 {len(occupied)} 个频道有人（合计 {total} 人）：")
    for row in occupied:
        lines.append(f"· {row['name']} {row['count']}人")
    return "\n".join(lines)


def resolve_channel(rows: list[dict[str, Any]], name_or_id: str) -> dict[str, Any] | None:
    """按频道名或 ID 找频道；优先精确名，再精确 ID，最后模糊名。"""
    text = (name_or_id or "").strip()
    if not text:
        return None
    for row in rows:
        if row.get("name") == text or row.get("id") == text:
            return row
    lowered = text.lower()
    for row in rows:
        name = (row.get("name") or "").lower()
        if lowered and lowered in name:
            return row
    return None

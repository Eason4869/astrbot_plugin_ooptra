"""Oopz 语音桥 — 在 QQ 查询/管理 Oopz 语音频道。

依赖 Ooptra ≥ 2.0.0 的 VOICE_API 契约：
  GET  /voice/status
  GET  /voice/channels?area=        # 域内语音频道 + 在线人数
  GET  /voice/members?area=&channel=
  POST /voice/join   {"area","channel"}
  POST /voice/leave
  GET  /health
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .ooptra_client import (
    OoptraClient,
    OoptraError,
    channel_rows,
    format_channel_counts,
    format_members,
    format_status,
    looks_like_id,
    looks_like_label,
    resolve_channel,
    resolve_group_mapping,
)

# 进/退语音最短间隔（秒），防止刷指令
JOIN_COOLDOWN_SEC = 3.0


class OoptraPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._client = self._build_client()
        self._map_lock = asyncio.Lock()
        self._voice_op_lock = asyncio.Lock()
        self._last_voice_op_at = 0.0

    # ---------- 基础设施 ----------

    def _timeout(self) -> float:
        raw = self.config.get("timeout_sec")
        try:
            value = float(raw) if raw is not None else 8.0
        except (TypeError, ValueError):
            value = 8.0
        return value if value > 0 else 8.0

    def _api_base(self) -> str:
        return str(self.config.get("api_base") or "http://127.0.0.1:3090").rstrip("/")

    def _api_token(self) -> str:
        return str(self.config.get("api_token") or "").strip()

    def _build_client(self) -> OoptraClient:
        return OoptraClient(
            base_url=self._api_base(),
            token=self._api_token(),
            timeout=self._timeout(),
        )

    @property
    def client(self) -> OoptraClient:
        base = self._api_base()
        token = self._api_token()
        timeout = self._timeout()
        if (
            self._client.base_url != base
            or self._client.token != token
            or self._client.timeout != timeout
        ):
            self._client = OoptraClient(base_url=base, token=token, timeout=timeout)
        return self._client

    def _group_id(self, event: AstrMessageEvent) -> str:
        return str(getattr(event.message_obj, "group_id", "") or "").strip()

    def _mapping(self, event: AstrMessageEvent, *, preferred_area: str = "") -> dict[str, Any] | None:
        return resolve_group_mapping(
            self.config.get("group_map") or {},
            self._group_id(event),
            preferred_area=preferred_area,
        )

    async def _ooptra_defaults(self) -> dict[str, str]:
        """读取 Ooptra 侧默认域/默认频道（WebUI「设为默认」写入）。"""
        try:
            data = await self.client.status()
        except OoptraError:
            return {"default_area": "", "default_channel": ""}
        return {
            "default_area": str(data.get("default_area") or ""),
            "default_channel": str(data.get("default_channel") or ""),
        }

    async def _require_mapping(self, event: AstrMessageEvent) -> dict[str, Any]:
        defaults = await self._ooptra_defaults()
        mapping = self._mapping(event, preferred_area=defaults.get("default_area", ""))
        if not mapping:
            group_id = self._group_id(event) or "（私聊）"
            return {
                "_error": (
                    f"群 {group_id} 未绑定 Oopz 频道。"
                    "管理员可发送：/语音绑定 <域ID> [频道ID] [备注]"
                )
            }
        if not mapping.get("area"):
            return {"_error": "当前群映射缺少 area（域 ID），请检查插件配置或重新 /语音绑定"}
        mapping["_default_area"] = str(defaults.get("default_area") or "")
        mapping["_default_channel"] = self._usable_default_channel(mapping, defaults)
        return mapping

    @staticmethod
    def _usable_default_channel(mapping: dict[str, Any], defaults: dict[str, str]) -> str:
        """Ooptra 的 default_channel 属于**它的** default_area。

        群绑的是别的域时，直接拿这个默认频道进房就是跨域（多域群必踩）。
        只有域一致（或 Ooptra 没报默认域）才认它；真正进房前 `_resolve_join_target`
        还会到域内频道表里再核一次。
        """
        channel = str(defaults.get("default_channel") or "").strip()
        if not channel:
            return ""
        default_area = str(defaults.get("default_area") or "").strip()
        area = str(mapping.get("area") or "").strip()
        if default_area and area and default_area != area:
            return ""
        return channel

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        try:
            return bool(event.is_admin())
        except Exception:
            return False

    def _can_control_voice(self, event: AstrMessageEvent) -> bool:
        if not self.config.get("allow_join", True):
            return False
        if bool(self.config.get("join_admin_only")):
            return self._is_admin(event)
        return True

    def _llm_tools_enabled(self) -> bool:
        return bool(self.config.get("enable_llm_tools", True))

    def _allow_arbitrary_channel(self) -> bool:
        return bool(self.config.get("allow_arbitrary_channel"))

    def _err(self, exc: Exception) -> str:
        logger.warning("ooptra plugin error: %s", exc)
        return f"调用 Ooptra 失败：{exc}"

    def _check_voice_cooldown(self) -> str | None:
        now = time.monotonic()
        elapsed = now - self._last_voice_op_at
        if elapsed < JOIN_COOLDOWN_SEC:
            wait = int(JOIN_COOLDOWN_SEC - elapsed + 0.999)
            return f"操作过于频繁，请 {wait} 秒后再试。"
        return None

    def _mark_voice_op(self) -> None:
        self._last_voice_op_at = time.monotonic()

    async def _mutate_group_map(self, mutate) -> dict[str, Any]:
        async with self._map_lock:
            group_map = dict(self.config.get("group_map") or {})
            mutate(group_map)
            self.config["group_map"] = group_map
            self.config.save_config()
            return group_map

    # ---------- 指令：状态 / 成员 ----------

    @filter.command("语音状态", alias={"语音人数", "voice_status", "语音频道"})
    async def voice_status(self, event: AstrMessageEvent):
        """查询本群绑定域内各语音频道的在线人数（无需配置默认频道）。"""
        mapping = await self._require_mapping(event)
        if mapping.get("_error"):
            yield event.plain_result(str(mapping["_error"]))
            return

        area = mapping["area"]
        label = mapping.get("label") or "语音频道"

        parts: list[str] = []
        try:
            status = await self.client.status()
            parts.append(format_status(status))
        except OoptraError as exc:
            parts.append(f"语音状态：获取失败（{exc}）")

        try:
            data = await self.client.channels(area)
            parts.append(format_channel_counts(data, label=label))
        except OoptraError as exc:
            parts.append(self._err(exc))

        yield event.plain_result("\n\n".join(p for p in parts if p))

    @filter.command("语音绑定", alias={"voice_bind", "绑定语音"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def voice_bind(
        self,
        event: AstrMessageEvent,
        area: str = "",
        channel: str = "",
        label: str = "",
    ):
        """绑定当前 QQ 群到 Oopz 域/频道。用法：/语音绑定 域ID [频道ID] [备注]"""
        group_id = self._group_id(event)
        if not group_id:
            yield event.plain_result("请在 QQ 群内使用该指令。")
            return

        area = (area or "").strip()
        channel = (channel or "").strip()
        label = (label or "").strip()

        # 兼容：/语音绑定 域ID 备注  → 把中文“频道”纠正为备注
        if channel and not label and looks_like_label(channel):
            label, channel = channel, ""

        if not area:
            yield event.plain_result(
                "用法：/语音绑定 <域ID> [频道ID] [备注]\n"
                "示例：/语音绑定 6ad0261b... chan-uid-xxx 开黑房\n"
                "解绑：/语音解绑"
            )
            return
        if area and not looks_like_id(area):
            yield event.plain_result("域 ID 格式看起来不对（应为 ID，不要用中文备注）。")
            return
        if channel and not looks_like_id(channel):
            yield event.plain_result("频道 ID 格式看起来不对（应为 ID，不要用中文备注）。")
            return

        def mutate(group_map: dict[str, Any]) -> None:
            group_map[str(group_id)] = {
                "area": area,
                "channel": channel,
                "label": label,
            }

        try:
            await self._mutate_group_map(mutate)
        except Exception as exc:
            logger.warning("save group_map failed: %s", exc)
            yield event.plain_result(f"绑定失败（保存配置出错）：{exc}")
            return

        shown = f"{area}" + (f" / {channel}" if channel else " /（默认频道未设）")
        extra = f"（{label}）" if label else ""
        yield event.plain_result(f"已绑定群 {group_id} → Oopz {shown}{extra}")

    @filter.command("语音解绑", alias={"voice_unbind", "解绑语音"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def voice_unbind(self, event: AstrMessageEvent):
        """解绑当前 QQ 群的 Oopz 语音映射。"""
        group_id = self._group_id(event)
        if not group_id:
            yield event.plain_result("请在 QQ 群内使用该指令。")
            return

        def mutate(group_map: dict[str, Any]) -> None:
            group_map.pop(str(group_id), None)
            try:
                group_map.pop(int(group_id), None)  # type: ignore[arg-type]
            except Exception:
                pass

        try:
            await self._mutate_group_map(mutate)
        except Exception as exc:
            logger.warning("save group_map failed: %s", exc)
            yield event.plain_result(f"解绑失败（保存配置出错）：{exc}")
            return
        yield event.plain_result(f"已解绑群 {group_id} 的 Oopz 语音映射。")

    # ---------- 指令：进退房 ----------

    async def _resolve_join_target(
        self,
        event: AstrMessageEvent,
        mapping: dict[str, str],
        channel_arg: str,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        """返回 (area, channel_id, channel_name, error)。

        不带参数时进默认频道；指定参数时可填频道名或频道 ID。
        """
        area = mapping.get("area") or ""
        bound = (mapping.get("channel") or "").strip()
        requested = (channel_arg or "").strip()
        default_channel = str(mapping.get("_default_channel") or "").strip()

        if not area:
            return None, None, None, "当前映射缺少域 ID，请先 /语音绑定"

        if not requested:
            if not bound:
                if not default_channel:
                    return None, None, None, (
                        "未配置默认语音频道。\n"
                        "可在 Ooptra 控制台「语音台 → 会话控制」点「设为默认」，\n"
                        "或用 /进语音 <频道名> 指定，或先 /语音绑定 <域ID> <频道ID>"
                    )
                # 跨域保护：默认频道未必属于本群绑定的域，进房前先在域内频道表里核实
                try:
                    data = await self.client.channels(area)
                except OoptraError as exc:
                    return None, None, None, self._err(exc)
                hit = resolve_channel(channel_rows(data), default_channel)
                if hit is None:
                    return None, None, None, (
                        "Ooptra 的默认频道不属于本群绑定的域，已拒绝进入以免跨域。\n"
                        "请用 /进语音 <频道名> 指定，或 /语音绑定 <域ID> <频道ID>"
                    )
                target_id = str(hit.get("id") or default_channel)
                return area, target_id, str(hit.get("name") or target_id), None
            return area, bound, mapping.get("label") or bound, None

        if requested == bound:
            return area, bound, mapping.get("label") or bound, None

        # 已是形如 ID 的绑定值，直接走 ID 路径
        if looks_like_id(requested) and not looks_like_label(requested):
            if not self._allow_arbitrary_channel() and not self._is_admin(event):
                return None, None, None, (
                    "默认仅允许进入本群绑定的频道。\n"
                    "可让管理员：/语音绑定 更新默认频道，或由管理员使用 /进语音 <频道名>"
                )
            return area, requested, requested, None

        # 按频道名（或 ID）到域内频道表里找
        try:
            data = await self.client.channels(area)
        except OoptraError as exc:
            return None, None, None, self._err(exc)
        rows = channel_rows(data)
        hit = resolve_channel(rows, requested)
        if hit is None:
            names = "、".join(r["name"] for r in rows[:12] if r.get("name")) or "（无）"
            return None, None, None, (
                f"未找到频道「{requested}」。\n该域可用频道：{names}"
            )

        target_id = hit.get("id") or ""
        target_name = hit.get("name") or target_id
        if not target_id:
            return None, None, None, f"频道「{target_name}」缺少 ID，无法进入。"

        if target_id != bound and not self._allow_arbitrary_channel() and not self._is_admin(event):
            return None, None, None, (
                "默认仅允许进入本群绑定的频道。\n"
                "可让管理员：/语音绑定 更新默认频道，或由管理员使用 /进语音 <频道名>"
            )
        return area, target_id, target_name, None

    @filter.command("进语音", alias={"加入语音", "voice_join"})
    async def voice_join(self, event: AstrMessageEvent, channel: str = ""):
        """让 Bot 进入本群默认频道；也可 /进语音 <频道名或频道ID> 指定其他频道。"""
        if not self._can_control_voice(event):
            yield event.plain_result("当前不允许进语音（见插件配置 allow_join / join_admin_only）。")
            return

        cooldown = self._check_voice_cooldown()
        if cooldown:
            yield event.plain_result(cooldown)
            return

        mapping = await self._require_mapping(event)
        if mapping.get("_error"):
            yield event.plain_result(str(mapping["_error"]))
            return

        area, target_channel, target_name, err = await self._resolve_join_target(event, mapping, channel)
        if err:
            yield event.plain_result(err)
            return

        # yield 不能待在 async with 里：生成器一挂起，锁就被一直握着，
        # 期间并发的 /退语音 会全部阻塞。
        failure = ""
        async with self._voice_op_lock:
            try:
                await self.client.join(area or "", target_channel or "")
            except OoptraError as exc:
                failure = self._err(exc)
            else:
                self._mark_voice_op()
        if failure:
            yield event.plain_result(failure)
            return

        yield event.plain_result(
            f"已进语音：{target_name or mapping.get('label') or target_channel}"
        )

    @filter.command("退语音", alias={"离开语音", "voice_leave"})
    async def voice_leave(self, event: AstrMessageEvent):
        """让 Bot 离开当前 Oopz 语音频道。"""
        if not self._can_control_voice(event):
            yield event.plain_result("当前不允许退语音（见插件配置 allow_join / join_admin_only）。")
            return

        cooldown = self._check_voice_cooldown()
        if cooldown:
            yield event.plain_result(cooldown)
            return

        failure = ""
        async with self._voice_op_lock:
            try:
                await self.client.leave()
            except OoptraError as exc:
                failure = self._err(exc)
            else:
                self._mark_voice_op()
        if failure:
            yield event.plain_result(failure)
            return
        yield event.plain_result("已退出语音。")

    # ---------- 指令：连通性 / 帮助 ----------

    @filter.command("语音自检", alias={"ooptra_ping", "语音连接"})
    async def voice_ping(self, event: AstrMessageEvent):
        """检查 Ooptra VOICE_API 连通性，并逐条探测插件依赖的契约端点。"""
        lines = [f"Ooptra VOICE_API：{self._api_base()}"]
        try:
            data = await self.client.health()
        except OoptraError as exc:
            lines.append(f"❌ 无法访问：{exc}")
            lines.append(
                "检查：1) Ooptra 是否已启用 VOICE_API  "
                "2) api_base 是否正确（3090 与 WebUI 同端口，已含全部 VOICE_API）  "
                "3) api_token 是否与 Ooptra 侧一致（默认填 WEBUI_CONFIG.token）  "
                "4) 防火墙"
            )
            yield event.plain_result("\n".join(lines))
            return

        version = str(data.get("version") or "").strip()
        lines.append(
            "✅ 已连通"
            + (f" · 版本 {version}" if version else "")
            + f" · 数据来自 {data.get('via') or '/health'}"
        )
        if data.get("enabled") is False:
            lines.append("⚠️ Ooptra 侧语音未启用（voice.enabled=false），进/退语音会失败")

        mapping = await self._require_mapping(event)
        area = "" if mapping.get("_error") else str(mapping.get("area") or "")
        lines.append("")
        lines.append("契约探测：")
        lines.extend(await self._probe_contract(area))
        lines.append("说明：/voice/channels 需 Ooptra ≥ 2.0.0，缺失请升级 Ooptra。")
        yield event.plain_result("\n".join(lines))

    async def _probe_contract(self, area: str) -> list[str]:
        """逐条探测契约端点，把「服务不可用」与「服务缺端点」区分开。"""
        calls: list[tuple[str, Any]] = [
            ("GET /health", self.client.health),
            ("GET /voice/status", self.client.status),
        ]
        if area:
            calls.append(("GET /voice/channels", lambda: self.client.channels(area)))
            calls.append(("GET /voice/members", lambda: self.client.members(area, "")))
        out: list[str] = []
        for label, call in calls:
            try:
                await call()
            except OoptraError as exc:
                out.append(f"❌ {label} — {exc}")
            else:
                out.append(f"✅ {label}")
        if not area:
            out.append("⚠️ 当前群未绑定域，已跳过 /voice/channels 与 /voice/members（/语音绑定 后可重试）")
        return out

    @filter.command("语音帮助", alias={"ooptra_help", "语音指令"})
    async def voice_help(self, event: AstrMessageEvent):
        """查看 Oopz 语音桥指令说明。"""
        lines = [
            "Oopz 语音桥指令",
            "· /语音状态 — 查各频道在线人数（别名：语音人数）",
            "· /进语音 [频道名|频道ID] — 进默认频道，或指定频道",
            "· /退语音 — Bot 退语音",
            "· /语音绑定 <域ID> [频道ID] [备注] —（管理）绑群",
            "· /语音解绑 —（管理）解绑",
            "· /语音自检 — 测 Ooptra 连接并逐条探测契约端点",
        ]
        if self.config.get("join_admin_only"):
            lines.append("进/退语音：仅管理员")
        if not self.config.get("allow_join", True):
            lines.append("进/退语音：已禁用")
        if not self._llm_tools_enabled():
            lines.append("LLM 工具：已禁用")
        lines.append("提示：先在 Ooptra 开启 VOICE_API，并完成 QQ 群绑定。")
        yield event.plain_result("\n".join(lines))

    # ---------- LLM 工具 ----------

    @filter.llm_tool(name="query_oopz_voice_members")
    async def tool_members(self, event: AstrMessageEvent, area: str = "", channel: str = "") -> str:
        """查询 Oopz 语音频道在线人数与成员列表。

        Args:
            area(string): Oopz 域 ID；可为空，将使用当前 QQ 群绑定
            channel(string): Oopz 频道 ID；可为空，将使用当前 QQ 群绑定
        """
        if not self._llm_tools_enabled():
            return "LLM 语音工具已禁用。"
        try:
            a, c = await self._resolve_area_channel(event, area, channel)
            if not a or not c:
                return "缺少 area/channel，且当前会话未绑定完整默认频道。"
            data = await self.client.members(a, c)
            return format_members(data)
        except OoptraError as exc:
            return f"查询失败：{exc}"

    @filter.llm_tool(name="query_oopz_voice_status")
    async def tool_status(self, event: AstrMessageEvent) -> str:
        """查询 Bot 当前 Oopz 语音进房状态（是否在房、域/频道）。"""
        if not self._llm_tools_enabled():
            return "LLM 语音工具已禁用。"
        try:
            data = await self.client.status()
            return format_status(data)
        except OoptraError as exc:
            return f"查询失败：{exc}"

    @filter.llm_tool(name="join_oopz_voice")
    async def tool_join(self, event: AstrMessageEvent, area: str = "", channel: str = "") -> str:
        """让 Bot 加入 Oopz 语音频道（默认进本群默认频道，也可按频道名指定）。

        Args:
            area(string): Oopz 域 ID；可为空则用当前群绑定
            channel(string): Oopz 频道名或频道 ID；可为空则用默认频道
        """
        if not self._llm_tools_enabled():
            return "LLM 语音工具已禁用。"
        if not self._can_control_voice(event):
            return "当前策略不允许进语音。"
        cooldown = self._check_voice_cooldown()
        if cooldown:
            return cooldown
        try:
            raw_a, raw_c = (area or "").strip(), (channel or "").strip()
            if raw_a or raw_c:
                defaults = await self._ooptra_defaults()
                bound = self._mapping(event, preferred_area=defaults.get("default_area", "")) or {}
                if not (self._allow_arbitrary_channel() or self._is_admin(event)):
                    if raw_a and raw_a != (bound.get("area") or ""):
                        return "默认不允许加入未绑定的域。"
                    allowed = {
                        bound.get("channel") or "",
                        self._usable_default_channel(bound, defaults),
                    }
                    if raw_c and raw_c not in allowed:
                        return "默认不允许加入未绑定的频道。"
                a, c = raw_a or "", raw_c or ""
                if not a or not c:
                    filled_a, filled_c = await self._resolve_area_channel(event, a, c)
                    a, c = a or filled_a, c or filled_c
            else:
                mapping = await self._require_mapping(event)
                if mapping.get("_error"):
                    return str(mapping["_error"])
                a, c, _name, err = await self._resolve_join_target(event, mapping, "")
                if err:
                    return str(err)
            if not a or not c:
                return "缺少 area/channel，且当前会话未绑定完整默认频道。"
            async with self._voice_op_lock:
                await self.client.join(a, c)
                self._mark_voice_op()
            return f"已进语音：{c}"
        except OoptraError as exc:
            return f"进房失败：{exc}"

    @filter.llm_tool(name="leave_oopz_voice")
    async def tool_leave(self, event: AstrMessageEvent) -> str:
        """让 Bot 离开当前 Oopz 语音频道。"""
        if not self._llm_tools_enabled():
            return "LLM 语音工具已禁用。"
        if not self._can_control_voice(event):
            return "当前策略不允许退语音。"
        cooldown = self._check_voice_cooldown()
        if cooldown:
            return cooldown
        try:
            async with self._voice_op_lock:
                await self.client.leave()
                self._mark_voice_op()
            return "已退出语音。"
        except OoptraError as exc:
            return f"退房失败：{exc}"

    async def _resolve_area_channel(
        self, event: AstrMessageEvent, area: str, channel: str
    ) -> tuple[str, str]:
        """给 LLM 工具补全缺省的 area/channel。

        必须走 ``_require_mapping``：只有它会把 preferred_area（Ooptra 默认域）传进
        ``resolve_group_mapping``。旧实现直接 ``self._mapping(event)``，多域群里会
        挑到第一个域，与斜杠命令选中的域不一致（README 承诺按默认域择一）。
        """
        a = (area or "").strip()
        c = (channel or "").strip()
        if a and c:
            return a, c
        mapping = await self._require_mapping(event)
        if mapping.get("_error"):
            return a, c
        if not a:
            a = str(mapping.get("area") or "").strip()
        if not c:
            c = str(mapping.get("channel") or "").strip() or str(
                mapping.get("_default_channel") or ""
            ).strip()
        return a, c

    # ---------- 生命周期 ----------

    async def terminate(self):
        """插件卸载/停用时调用。"""
        self._client = self._build_client()
        self._last_voice_op_at = 0.0

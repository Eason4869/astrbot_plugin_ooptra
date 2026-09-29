"""Oopz 语音桥 — 在 QQ 查询/管理 Oopz 语音频道。

依赖 Ooptra 侧 VOICE_API 契约：
  GET  /voice/status
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

from ooptra_client import (
    OoptraClient,
    OoptraError,
    format_members,
    format_status,
    looks_like_id,
    looks_like_label,
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

    def _mapping(self, event: AstrMessageEvent) -> dict[str, str] | None:
        return resolve_group_mapping(self.config.get("group_map") or {}, self._group_id(event))

    def _require_mapping(self, event: AstrMessageEvent) -> dict[str, str]:
        mapping = self._mapping(event)
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
        return mapping

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
        """查询当前群绑定的 Oopz 语音频道人数与状态。"""
        mapping = self._require_mapping(event)
        if mapping.get("_error"):
            yield event.plain_result(mapping["_error"])
            return

        area = mapping["area"]
        channel = mapping.get("channel") or ""
        label = mapping.get("label") or channel or "语音频道"

        parts: list[str] = []
        try:
            status = await self.client.status()
            parts.append(format_status(status))
        except OoptraError as exc:
            parts.append(f"语音状态：获取失败（{exc}）")

        try:
            if not channel:
                parts.append(
                    f"当前绑定域：{area}\n未配置默认频道。请管理员 /语音绑定 <域ID> <频道ID> 补全，"
                    "或使用 /进语音 <频道ID>（需允许自定义频道）。"
                )
            else:
                data = await self.client.members(area, channel)
                parts.append(format_members(data, label=label))
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

    def _resolve_join_target(
        self,
        event: AstrMessageEvent,
        mapping: dict[str, str],
        channel_arg: str,
    ) -> tuple[str | None, str | None, str | None]:
        """返回 (area, channel, error)。"""
        area = mapping.get("area") or ""
        bound = (mapping.get("channel") or "").strip()
        requested = (channel_arg or "").strip()

        if not area:
            return None, None, "当前映射缺少域 ID，请先 /语音绑定"

        if not requested:
            if not bound:
                return None, None, (
                    "未配置默认语音频道。\n"
                    "用法：/进语音 <频道ID>（需允许自定义频道）\n"
                    "或先 /语音绑定 <域ID> <频道ID>"
                )
            return area, bound, None

        if requested == bound:
            return area, bound, None

        if not looks_like_id(requested):
            return None, None, "频道 ID 格式不正确。"

        if not self._allow_arbitrary_channel() and not self._is_admin(event):
            return None, None, (
                "默认仅允许进入本群绑定的频道。\n"
                "可让管理员：/语音绑定 更新默认频道，或由管理员使用 /进语音 <频道ID>"
            )
        return area, requested, None

    @filter.command("进语音", alias={"加入语音", "voice_join"})
    async def voice_join(self, event: AstrMessageEvent, channel: str = ""):
        """让 Bot 进入本群绑定的 Oopz 语音频道。管理员可指定其他频道 ID。"""
        if not self._can_control_voice(event):
            yield event.plain_result("当前不允许进语音（见插件配置 allow_join / join_admin_only）。")
            return

        cooldown = self._check_voice_cooldown()
        if cooldown:
            yield event.plain_result(cooldown)
            return

        mapping = self._require_mapping(event)
        if mapping.get("_error"):
            yield event.plain_result(mapping["_error"])
            return

        area, target_channel, err = self._resolve_join_target(event, mapping, channel)
        if err:
            yield event.plain_result(err)
            return

        async with self._voice_op_lock:
            try:
                result = await self.client.join(area or "", target_channel or "")
            except OoptraError as exc:
                yield event.plain_result(self._err(exc))
                return
            self._mark_voice_op()

        label = mapping.get("label") or target_channel
        yield event.plain_result(
            f"已请求进入语音：{label}（{area} / {target_channel}）\n详情：{result}"
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

        async with self._voice_op_lock:
            try:
                result = await self.client.leave()
            except OoptraError as exc:
                yield event.plain_result(self._err(exc))
                return
            self._mark_voice_op()
        yield event.plain_result(f"已请求退出语音。\n详情：{result}")

    # ---------- 指令：连通性 / 帮助 ----------

    @filter.command("语音自检", alias={"ooptra_ping", "语音连接"})
    async def voice_ping(self, event: AstrMessageEvent):
        """检查 Ooptra VOICE_API 连通性。"""
        try:
            data = await self.client.health()
            yield event.plain_result(f"Ooptra VOICE_API 正常。\n{data}")
        except OoptraError as exc:
            yield event.plain_result(
                "无法访问 Ooptra VOICE_API。\n"
                f"原因：{exc}\n"
                "检查：1) Ooptra 是否已启用 VOICE_API  2) api_base/token 是否一致  3) 防火墙"
            )

    @filter.command("语音帮助", alias={"ooptra_help", "语音指令"})
    async def voice_help(self, event: AstrMessageEvent):
        """查看 Oopz 语音桥指令说明。"""
        lines = [
            "Oopz 语音桥指令",
            "· /语音状态 — 查人数与状态（别名：语音人数）",
            "· /进语音 [频道ID] — Bot 进语音（默认仅绑定频道）",
            "· /退语音 — Bot 退语音",
            "· /语音绑定 <域ID> [频道ID] [备注] —（管理）绑群",
            "· /语音解绑 —（管理）解绑",
            "· /语音自检 — 测 Ooptra 连接",
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
            a, c = self._resolve_area_channel(event, area, channel)
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
        """让 Bot 加入 Oopz 语音频道（默认仅本群绑定频道）。

        Args:
            area(string): Oopz 域 ID；可为空则用当前群绑定
            channel(string): Oopz 频道 ID；可为空则用当前群绑定
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
                bound = self._mapping(event) or {}
                if not (self._allow_arbitrary_channel() or self._is_admin(event)):
                    if raw_a and raw_a != (bound.get("area") or ""):
                        return "默认不允许加入未绑定的域。"
                    if raw_c and raw_c != (bound.get("channel") or ""):
                        return "默认不允许加入未绑定的频道。"
                a, c = raw_a or "", raw_c or ""
                if not a or not c:
                    filled_a, filled_c = self._resolve_area_channel(event, a, c)
                    a, c = a or filled_a, c or filled_c
            else:
                mapping = self._require_mapping(event)
                if mapping.get("_error"):
                    return mapping["_error"]
                a, c, err = self._resolve_join_target(event, mapping, "")
                if err:
                    return err
            if not a or not c:
                return "缺少 area/channel，且当前会话未绑定完整默认频道。"
            async with self._voice_op_lock:
                result = await self.client.join(a, c)
                self._mark_voice_op()
            return f"已请求进入 {a}/{c}。结果：{result}"
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
                result = await self.client.leave()
                self._mark_voice_op()
            return f"已请求退出语音。结果：{result}"
        except OoptraError as exc:
            return f"退房失败：{exc}"

    def _resolve_area_channel(
        self, event: AstrMessageEvent, area: str, channel: str
    ) -> tuple[str, str]:
        a = (area or "").strip()
        c = (channel or "").strip()
        if a and c:
            return a, c
        mapping = self._mapping(event) or {}
        if not a:
            a = str(mapping.get("area") or "").strip()
        if not c:
            c = str(mapping.get("channel") or "").strip()
        return a, c

    # ---------- 生命周期 ----------

    async def terminate(self):
        """插件卸载/停用时调用。"""
        self._client = self._build_client()
        self._last_voice_op_at = 0.0

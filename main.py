"""Ooptra 语音桥 — 在 QQ 查询/管理 Oopz 语音频道。

依赖 Ooptra 侧 VOICE_API（项目 1）契约：
  GET  /voice/status
  GET  /voice/members?area=&channel=
  POST /voice/join   {"area","channel"}
  POST /voice/leave
  GET  /health
"""

from __future__ import annotations

from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from ooptra_client import (
    OoptraClient,
    OoptraError,
    format_members,
    format_status,
    resolve_group_mapping,
)


class OoptraPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._client = self._build_client()

    # ---------- 基础设施 ----------

    def _build_client(self) -> OoptraClient:
        return OoptraClient(
            base_url=str(self.config.get("api_base") or "http://127.0.0.1:3091"),
            token=str(self.config.get("api_token") or ""),
            timeout=float(self.config.get("timeout_sec") or 8.0),
        )

    def _reload_client(self) -> OoptraClient:
        self._client = self._build_client()
        return self._client

    @property
    def client(self) -> OoptraClient:
        # 配置热更后重建，避免 token/base 陈旧
        base = str(self.config.get("api_base") or "http://127.0.0.1:3091")
        token = str(self.config.get("api_token") or "")
        timeout = float(self.config.get("timeout_sec") or 8.0)
        if (
            self._client.base_url != base.rstrip("/")
            or self._client.token != token.strip()
            or self._client.timeout != timeout
        ):
            return self._reload_client()
        return self._client

    def _group_id(self, event: AstrMessageEvent) -> str:
        return str(getattr(event.message_obj, "group_id", "") or "").strip()

    def _mapping(self, event: AstrMessageEvent) -> dict[str, str] | None:
        return resolve_group_mapping(self.config.get("group_map") or {}, self._group_id(event))

    def _require_mapping(self, event: AstrMessageEvent) -> dict[str, str] | None:
        mapping = self._mapping(event)
        if not mapping:
            group_id = self._group_id(event) or "（私聊）"
            return {"_error": f"群 {group_id} 未绑定 Oopz 频道。管理员可发送：/voice_bind <域ID> <频道ID> [备注]"}
        if not mapping.get("area"):
            return {"_error": "当前群映射缺少 area（域 ID），请检查插件配置或重新 /voice_bind"}
        return mapping

    def _can_control_voice(self, event: AstrMessageEvent) -> bool:
        if not self.config.get("allow_join", True):
            return False
        if bool(self.config.get("join_admin_only")):
            try:
                return bool(event.is_admin())
            except Exception:
                return False
        return True

    def _err(self, event: AstrMessageEvent, exc: Exception) -> str:
        logger.warning("ooptra plugin error: %s", exc)
        return f"调用 Ooptra 失败：{exc}"

    # ---------- 指令：状态 / 成员 ----------

    @filter.command("语音状态", alias={"语音人数", "voice_status", "语音频道"})
    async def voice_status(self, event: AstrMessageEvent):
        """查询当前群绑定的 Oopz 语音频道人数与状态。"""
        mapping = self._require_mapping(event)
        if mapping is None or mapping.get("_error"):
            yield event.plain_result((mapping or {}).get("_error", "未绑定"))
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
                    f"当前绑定域：{area}\n未配置默认频道。可用：/voice_join <频道ID>\n或 /voice_bind 补全频道。"
                )
            else:
                data = await self.client.members(area, channel)
                parts.append(format_members(data, label=label))
        except OoptraError as exc:
            parts.append(self._err(event, exc))

        yield event.plain_result("\n\n".join(p for p in parts if p))

    @filter.command("语音绑定", alias={"voice_bind", "绑定语音"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def voice_bind(self, event: AstrMessageEvent, area: str = "", channel: str = "", label: str = ""):
        """绑定当前 QQ 群到 Oopz 域/频道：/voice_bind 域ID 频道ID [备注]"""
        group_id = self._group_id(event)
        if not group_id:
            yield event.plain_result("请在 QQ 群内使用该指令。")
            return
        area = (area or "").strip()
        channel = (channel or "").strip()
        label = (label or "").strip()
        if not area:
            yield event.plain_result(
                "用法：/voice_bind <域ID> <频道ID> [备注]\n"
                "只需绑定域：/voice_bind <域ID>\n"
                "解绑：/voice_unbind"
            )
            return

        group_map = dict(self.config.get("group_map") or {})
        group_map[str(group_id)] = {"area": area, "channel": channel, "label": label}
        self.config["group_map"] = group_map
        try:
            self.config.save_config()
        except Exception as exc:
            logger.warning("save group_map failed: %s", exc)
            yield event.plain_result(f"绑定失败（保存配置出错）：{exc}")
            return

        shown = f"{area}" + (f" / {channel}" if channel else " /（默认频道未设）")
        yield event.plain_result(f"已绑定群 {group_id} → Oopz {shown}" + (f"（{label}）" if label else ""))

    @filter.command("语音解绑", alias={"voice_unbind", "解绑语音"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def voice_unbind(self, event: AstrMessageEvent):
        """解绑当前 QQ 群的 Oopz 语音映射。"""
        group_id = self._group_id(event)
        if not group_id:
            yield event.plain_result("请在 QQ 群内使用该指令。")
            return
        group_map = dict(self.config.get("group_map") or {})
        key = str(group_id)
        if key not in group_map and group_id not in group_map:
            yield event.plain_result("当前群未绑定映射。")
            return
        group_map.pop(key, None)
        try:
            group_map.pop(int(group_id), None)  # type: ignore[arg-type]
        except Exception:
            pass
        self.config["group_map"] = group_map
        try:
            self.config.save_config()
        except Exception as exc:
            logger.warning("save group_map failed: %s", exc)
            yield event.plain_result(f"解绑失败（保存配置出错）：{exc}")
            return
        yield event.plain_result(f"已解绑群 {group_id} 的 Oopz 语音映射。")

    # ---------- 指令：进退房 ----------

    @filter.command("进语音", alias={"加入语音", "voice_join"})
    async def voice_join(self, event: AstrMessageEvent, channel: str = ""):
        """让 Bot 进入本群绑定的 Oopz 语音频道。可选参数指定频道 ID。"""
        if not self._can_control_voice(event):
            yield event.plain_result("当前不允许进语音（见插件配置 allow_join / join_admin_only）。")
            return

        mapping = self._require_mapping(event)
        if mapping is None or mapping.get("_error"):
            yield event.plain_result((mapping or {}).get("_error", "未绑定"))
            return

        area = mapping["area"]
        target_channel = (channel or "").strip() or mapping.get("channel") or ""
        if not target_channel:
            yield event.plain_result(
                "未配置默认语音频道。\n"
                "用法：/voice_join <频道ID>\n"
                "或先 /voice_bind <域ID> <频道ID>"
            )
            return

        try:
            result = await self.client.join(area, target_channel)
        except OoptraError as exc:
            yield event.plain_result(self._err(event, exc))
            return

        label = mapping.get("label") or target_channel
        yield event.plain_result(f"已请求进入语音：{label}（{area} / {target_channel}）\n详情：{result}")

    @filter.command("退语音", alias={"离开语音", "voice_leave"})
    async def voice_leave(self, event: AstrMessageEvent):
        """让 Bot 离开当前 Oopz 语音频道。"""
        if not self._can_control_voice(event):
            yield event.plain_result("当前不允许退语音（见插件配置 allow_join / join_admin_only）。")
            return
        try:
            result = await self.client.leave()
        except OoptraError as exc:
            yield event.plain_result(self._err(event, exc))
            return
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
        """查看 Ooptra 语音桥指令说明。"""
        status_cmd = self.config.get("cmd_voice_status") or "语音状态"
        join_cmd = self.config.get("cmd_voice_join") or "进语音"
        leave_cmd = self.config.get("cmd_voice_leave") or "退语音"
        yield event.plain_result(
            "Ooptra 语音桥指令\n"
            f"· /{status_cmd} — 查人数与状态\n"
            f"· /{join_cmd} [频道ID] — Bot 进语音\n"
            f"· /{leave_cmd} — Bot 退语音\n"
            "· /语音绑定 <域ID> [频道ID] [备注] —（管理）绑群\n"
            "· /语音解绑 —（管理）解绑\n"
            "· /语音自检 — 测 Ooptra 连接\n"
            "提示：先在 Ooptra 开启 VOICE_API，并完成 QQ 群绑定。"
        )

    # ---------- LLM 工具 ----------

    @filter.llm_tool(name="query_oopz_voice_members")
    async def tool_members(self, event: AstrMessageEvent, area: str = "", channel: str = "") -> str:
        """查询 Oopz 语音频道在线人数与成员列表。

        Args:
            area(string): Oopz 域 ID；可为空，将使用当前 QQ 群绑定
            channel(string): Oopz 频道 ID；可为空，将使用当前 QQ 群绑定
        """
        try:
            a, c = self._resolve_area_channel(event, area, channel)
            if not c:
                return "缺少 channel（频道 ID），且当前会话未绑定默认频道。"
            data = await self.client.members(a, c)
            return format_members(data)
        except OoptraError as exc:
            return f"查询失败：{exc}"

    @filter.llm_tool(name="query_oopz_voice_status")
    async def tool_status(self, event: AstrMessageEvent) -> str:
        """查询 Bot 当前 Oopz 语音进房状态（是否在房、域/频道）。

        Args:
            dummy(string): 保留字段，请传空字符串
        """
        try:
            data = await self.client.status()
            return format_status(data)
        except OoptraError as exc:
            return f"查询失败：{exc}"

    @filter.llm_tool(name="join_oopz_voice")
    async def tool_join(self, event: AstrMessageEvent, area: str = "", channel: str = "") -> str:
        """让 Bot 加入指定 Oopz 语音频道。

        Args:
            area(string): Oopz 域 ID；可为空则用当前群绑定
            channel(string): Oopz 频道 ID；可为空则用当前群绑定
        """
        if not self._can_control_voice(event):
            return "当前策略不允许进语音。"
        try:
            a, c = self._resolve_area_channel(event, area, channel)
            if not c:
                return "缺少 channel，且未绑定默认频道。"
            result = await self.client.join(a, c)
            return f"已请求进入 {a}/{c}。结果：{result}"
        except OoptraError as exc:
            return f"进房失败：{exc}"

    @filter.llm_tool(name="leave_oopz_voice")
    async def tool_leave(self, event: AstrMessageEvent) -> str:
        """让 Bot 离开当前 Oopz 语音频道。

        Args:
            _unused(string): 无需参数，保留占位
        """
        if not self._can_control_voice(event):
            return "当前策略不允许退语音。"
        try:
            result = await self.client.leave()
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

"""AstrBot control panel services; Ooptra remains the external API authority."""

from __future__ import annotations

import re
from typing import Any

from .ooptra_client import (
    BACKEND_LABELS,
    OoptraError,
    channel_rows,
    looks_like_id,
    normalize_backend,
    resolve_group_mapping,
)

# The full console can call only these known Ooptra routes, never arbitrary URLs.
CONSOLE_ROUTES = {
    "GET": {"/api/status", "/api/credentials", "/api/update", "/api/logs", "/api/logs/tail",
            "/api/config", "/api/login/browser", "/api/voice/status", "/api/voice/members",
            "/api/oopz/areas", "/api/oopz/channels", "/api/persona", "/api/memory", "/api/voice/auto-visit",
            "/api/voice/preview/prompts", "/api/maintenance"},
    "POST": {"/api/config", "/api/login/browser", "/api/login/browser/cancel", "/api/login/api",
             "/api/bridge/restart", "/api/voice/join", "/api/voice/leave", "/api/voice/speak",
             "/api/voice/auto-visit/config", "/api/voice/auto-visit/pause", "/api/voice/auto-visit/resume",
             "/api/voice/diagnostics", "/api/voice/preview", "/api/maintenance/check",
             "/api/maintenance/backups", "/api/maintenance/update", "/api/maintenance/restore"},
    "PUT": {"/api/persona"},
    "DELETE": {"/api/memory"},
}


def _object(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("请求内容必须是对象。")
    return value


def _id(value: Any, label: str, *, optional: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label}必须是 ID。")
    result = value.strip()
    if optional and not result:
        return ""
    if not looks_like_id(result):
        raise ValueError(f"{label}格式不正确，请填写 ID。")
    return result


def _group(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value.strip()):
        raise ValueError("请填写有效的 QQ 群号。")
    return value.strip()


def _confirmed(result: dict) -> dict:
    if not isinstance(result, dict):
        raise OoptraError("Ooptra 返回了无法识别的响应。")
    if result.get("ok") is not True:
        raise OoptraError(str(result.get("error") or "Ooptra 未确认操作成功。"))
    return result


class PanelInactive(ValueError):
    """A retained page or queued write belongs to a stopped plugin instance."""


class ControlPanel:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def ensure_active(self) -> None:
        if not self.plugin._webui_active:
            raise PanelInactive("Ooptra 插件已停用，请启用插件并重新打开页面。")

    def bootstrap(self) -> dict:
        config = self.plugin.config
        mappings = config.get("group_map") or {}
        bindings = []
        for key in mappings:
            row = resolve_group_mapping(mappings, str(key))
            bindings.append({"group_id": str(key), **(row or {"area": "", "areas": [], "channel": "", "label": ""})})
        return {"bindings": bindings, "allow_join": bool(config.get("allow_join", True))}

    async def status(self) -> dict:
        data = await self.plugin.client.status()
        fields = ("ok", "joined", "enabled", "backend", "area", "channel", "state", "default_area", "default_channel", "auto_visit")
        return {key: data[key] for key in fields if key in data}

    async def areas(self) -> dict:
        return await self.plugin.client.areas()

    async def channels(self, area: str) -> dict:
        return await self.plugin.client.channels(_id(area, "域 ID"))

    async def members(self, area: str, channel: str) -> dict:
        return await self.plugin.client.members(_id(area, "域 ID"), _id(channel, "频道 ID"))

    async def save_binding(self, value: Any) -> dict:
        self.ensure_active()
        body = _object(value)
        group = _group(body.get("group_id"))
        areas = body.get("areas")
        if not isinstance(areas, list) or not 1 <= len(areas) <= 16:
            raise ValueError("请填写 1 至 16 个 Oopz 域 ID。")
        areas = list(dict.fromkeys(_id(area, "域 ID") for area in areas))
        channel = _id(body.get("channel", ""), "频道 ID", optional=True)
        label = body.get("label", "")
        if not isinstance(label, str) or len(label) > 80:
            raise ValueError("备注不能超过 80 个字。")

        def mutate(mappings):
            self.ensure_active()
            old = mappings.get(group, mappings.get(int(group)))
            row = dict(old) if isinstance(old, dict) else {}
            row.pop("area", None)
            row.pop("areas", None)
            row.update({"channel": channel, "label": label.strip()})
            row["areas" if len(areas) > 1 else "area"] = areas if len(areas) > 1 else areas[0]
            mappings.pop(int(group), None)
            mappings[group] = row

        await self.plugin._mutate_group_map(mutate)
        return {"ok": True, "message": "群绑定已保存。", **self.bootstrap()}

    async def delete_binding(self, value: Any) -> dict:
        self.ensure_active()
        group = _group(_object(value).get("group_id"))

        def mutate(mappings):
            self.ensure_active()
            mappings.pop(group, None)
            mappings.pop(int(group), None)

        await self.plugin._mutate_group_map(mutate)
        return {"ok": True, "message": "群绑定已移除。", **self.bootstrap()}

    async def _verify_channel(self, area: str, channel: str) -> None:
        data = await self.plugin.client.channels(area)
        if not any(row.get("id") == channel for row in channel_rows(data)):
            raise ValueError("所选频道不属于这个域，请刷新频道后重试。")

    async def action(self, value: Any) -> dict:
        self.ensure_active()
        body = _object(value)
        action = body.get("action")
        if not isinstance(action, str) or action not in {"join", "leave", "backend", "auto_visit"}:
            raise ValueError("不支持的语音操作。")
        if action in {"join", "leave"} and not self.plugin.config.get("allow_join", True):
            raise ValueError("插件已关闭进退语音，请先修改插件配置 allow_join。")
        area = _id(body.get("area"), "域 ID") if action in {"join", "auto_visit"} else ""
        channel = _id(body.get("channel"), "频道 ID") if action == "join" else ""
        backend = normalize_backend(str(body.get("backend", ""))) if action == "backend" else ""
        enabled = body.get("enabled")
        if action == "auto_visit" and not isinstance(enabled, bool):
            raise ValueError("串门开关必须为布尔值。")

        async with self.plugin._voice_op_lock:
            self.ensure_active()
            cooldown = self.plugin._check_voice_cooldown()
            if cooldown:
                raise ValueError(cooldown)
            if action == "join":
                await self._verify_channel(area, channel)
                result = _confirmed(await self.plugin.client.join(area, channel))
                message = "已进入所选语音频道。"
            elif action == "leave":
                result = _confirmed(await self.plugin.client.leave())
                message = "已退出语音。"
            elif action == "backend":
                result = await self.plugin.client.set_backend(backend)
                notes = result.get("notes") or []
                applied = ("backend" in (result.get("hot_reloaded_fields") or []) and not result.get("restart_required")
                           and not any("失败" in str(note) or "错误" in str(note) for note in notes))
                message = f"语音方案已保存为 {BACKEND_LABELS[backend]}（全局）。"
                if result.get("restart_required"):
                    message += "需要重启 Ooptra 后生效。"
                elif not applied:
                    message += "接口未确认运行时切换，请检查完整控制台。"
            else:
                result = await self.plugin.client.set_auto_visit(area, enabled)
                message = f"这个域的自动串门已{'开启' if enabled else '关闭'}。"
                if enabled and result["status"].get("paused"):
                    message += "控制器仍处于暂停状态，请在完整控制台检查后恢复。"
            self.plugin._mark_voice_op()
        return {"ok": True, "message": message, "result": result,
                "applied": applied if action == "backend" else True}

    async def console(self, value: Any) -> dict:
        self.ensure_active()
        body = _object(value)
        method, path = body.get("method", "GET"), body.get("path")
        if not isinstance(method, str) or not isinstance(path, str) or path not in CONSOLE_ROUTES.get(method, set()):
            raise ValueError("不支持的控制台接口。")
        params = _object(body.get("params", {}))
        if any(key not in {"area", "channel", "file", "lines", "force", "kind"} for key in params):
            raise ValueError("不支持的控制台查询参数。")
        payload = _object(body.get("body", {})) if method != "GET" else None
        if path in {"/api/voice/join", "/api/voice/leave"}:
            action = {**(payload or {}), "action": path.rsplit("/", 1)[1]}
            if action["action"] == "join" and (not action.get("area") or not action.get("channel")):
                status = await self.plugin.client.status()
                action["area"] = action.get("area") or status.get("default_area", "")
                if not action.get("channel") and action["area"] == status.get("default_area"):
                    action["channel"] = status.get("default_channel", "")
            return (await self.action(action))["result"]
        if method == "GET":
            return await self.plugin.client._request(method, path, params=params)
        # Serialize console writes, but preserve config-save + immediate reconnect.
        # Voice changes share the command cooldown; account/persona/reconnect do not.
        updates = payload.get("updates", {})
        independent = path in {"/api/voice/preview", "/api/voice/diagnostics"}
        if independent:
            # Isolated browser previews do not mutate the room or command cooldown.
            return _confirmed(await self.plugin.client._request(method, path, params=params, json_body=payload,
                                                                timeout_sec=max(self.plugin.client.timeout, 55)))
        voice_change = path.startswith("/api/voice/") or (
            path == "/api/config" and isinstance(updates, dict) and "voice" in updates
        )
        async with self.plugin._voice_op_lock:
            self.ensure_active()
            cooldown = self.plugin._check_voice_cooldown() if voice_change else None
            if cooldown:
                raise ValueError(cooldown)
            kwargs = {"timeout_sec": max(self.plugin.client.timeout, 55)} if path.startswith("/api/maintenance/") else {}
            result = _confirmed(await self.plugin.client._request(method, path, params=params, json_body=payload, **kwargs))
            if voice_change:
                self.plugin._mark_voice_op()
            return result

    async def backup(self, backup_id: str) -> bytes:
        self.ensure_active()
        if not isinstance(backup_id, str) or not re.fullmatch(r"[a-f0-9]{32}", backup_id):
            raise ValueError("备份 ID 无效。")
        result = await self.plugin.client.backup_bytes(backup_id)
        self.ensure_active()
        return result

    async def logs(self, params: dict):
        self.ensure_active()
        file = params.get("file", "")
        lines = params.get("lines", "300")
        if not isinstance(file, str) or len(file) > 128 or "/" in file or "\\" in file or ".." in file:
            raise ValueError("日志文件名格式不正确。")
        try:
            count = min(20000, max(1, int(lines)))
        except (TypeError, ValueError) as exc:
            raise ValueError("日志行数必须为整数。") from exc
        async for chunk in self.plugin.client.stream_logs(file, count):
            yield chunk

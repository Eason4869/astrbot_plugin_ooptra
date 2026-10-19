"""Resolve existing QQ bindings and serve Ooptra context RPC without posting chat messages."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from .ooptra_client import resolve_group_mapping
from .sync_state import SyncState
from .voice_memory import MemoryUnavailable, discover

logger = logging.getLogger(__name__)


class ContextSync:
    def __init__(self, plugin, *, state_path: Path | None = None):
        self.plugin = plugin
        self.state = SyncState(state_path)
        self.task: asyncio.Task | None = None
        self.error = ""
        self.last_operation = ""
        self._lock = asyncio.Lock()
        self._jobs: set[asyncio.Task] = set()
        self.client_id = ""

    def start(self):
        if self.task is None:
            self.client_id = self.state.get("client_id") or uuid.uuid4().hex
            self.state.put("client_id", self.client_id)
            self.task = asyncio.create_task(self._worker(), name="ooptra-astrbot-context")

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        for job in self._jobs:
            job.cancel()
        if self._jobs:
            await asyncio.gather(*self._jobs, return_exceptions=True)
        self._jobs.clear()

    def observe(self, event):
        group = str(event.get_group_id() or "")
        if group and event.get_platform_name() == "aiocqhttp":
            self.state.put("source:" + event.get_platform_id() + ":" + group,
                           {"bot_id": event.get_self_id(), "umo": event.unified_msg_origin,
                            "member_session": str(event.get_session_id()) != group})

    def _provider(self):
        rows = discover(self.plugin.context)
        selected = str(self.plugin.config.get("context_memory_provider") or "auto")
        if selected == "none":
            return "", None, "已选择只同步人格"
        if not rows and selected == "auto":
            return "", None, "未检测到受支持的记忆插件"
        if selected == "auto" and len(rows) > 1:
            return "", None, "检测到多个记忆插件，请选择主记忆插件"
        row = next((row for row in rows if selected == "auto" or row["id"] == selected), None)
        if row is None:
            return "", None, "所选记忆插件未启用"
        return row["id"], row.get("adapter"), row.get("error", "")

    def describe(self):
        _, _, error = self._provider()
        manager = getattr(self.plugin.context, "persona_manager", None)
        personas = [{"id": row["name"], "name": row["name"]}
                    for row in getattr(manager, "personas_v3", []) if row.get("name")]
        return {"enabled": bool(self.plugin.config.get("context_sync_enabled", False)),
                "personas": personas, "providers": [
                    {key: value for key, value in row.items() if key != "adapter"}
                    for row in discover(self.plugin.context)], "memory_error": error,
                "error": self.error, "last_operation": self.last_operation,
                "persona_id": self.plugin.config.get("context_persona_id", ""),
                "memory_provider": self.plugin.config.get("context_memory_provider", "auto")}

    def _binding(self, room):
        if not isinstance(room, dict) or not room.get("area") or not room.get("channel"):
            raise ValueError("当前语音房间信息不完整")
        mappings = self.plugin.config.get("group_map") or {}
        matches = []
        for group in dict.fromkeys(str(key) for key in mappings):
            row = resolve_group_mapping(mappings, str(group), preferred_area=room["area"])
            if row and row["area"] == room["area"] and row.get("channel", "") in ("", room["channel"]):
                matches.append(str(group))
        if not matches:
            raise ValueError("当前语音频道没有绑定 QQ 群")
        if len(matches) != 1:
            raise ValueError("当前语音频道绑定了多个 QQ 群，请保留唯一来源绑定")
        return matches[0]

    def _source(self, group):
        mappings = self.plugin.config.get("group_map") or {}
        row = mappings.get(group, mappings.get(int(group), {}))
        override = row if isinstance(row, dict) else {}
        selected = str(override.get("platform_id") or self.plugin.config.get("context_platform_id") or "")
        manager = getattr(self.plugin.context, "platform_manager", None)
        platforms = manager.get_insts() if manager else []
        metas = [platform.meta() for platform in platforms if platform.meta().name == "aiocqhttp"
                 and (not selected or platform.meta().id == selected)]
        if len(metas) != 1:
            raise ValueError("无法唯一确定 QQ 平台实例，请配置 QQ 平台 ID")
        meta = metas[0]
        observed = self.state.get("source:" + meta.id + ":" + group) or {}
        if observed.get("member_session"):
            raise ValueError("绑定 QQ 群启用了成员独立会话，无法自动选择唯一群上下文")
        return {"group_id": group, "platform_id": meta.id,
                "umo": observed.get("umo") or f"{meta.id}:GroupMessage:{group}",
                "bot_id": observed.get("bot_id", "")}

    async def _persona(self, scope, *, follow_group=False):
        context = self.plugin.context
        manager = getattr(context, "persona_manager", None)
        if manager is None:
            raise ValueError("当前 AstrBot 不支持人格管理接口")
        raw = (self.plugin.config.get("group_map") or {}).get(scope["group_id"], {})
        selected = "" if follow_group else str(
            (raw.get("persona_id") if isinstance(raw, dict) else "") or
            self.plugin.config.get("context_persona_id") or "")
        if selected:
            persona = next((row for row in manager.personas_v3 if row["name"] == selected), None)
            if persona is None:
                raise ValueError("所选人格已删除或不可用，请重新选择")
            return selected, str(persona.get("prompt") or "")
        conversation_persona = None
        conversations = getattr(context, "conversation_manager", None)
        if conversations:
            cid = await conversations.get_curr_conversation_id(scope["umo"])
            if cid:
                conv = await conversations.get_conversation(scope["umo"], cid)
                conversation_persona = getattr(conv, "persona_id", None)
        selected, persona, _, _ = await manager.resolve_selected_persona(
            umo=scope["umo"], conversation_persona_id=conversation_persona, platform_name="aiocqhttp")
        return "" if selected in (None, "[%None]") else selected, str((persona or {}).get("prompt") or "")

    async def handle(self, event):
        if isinstance(event, dict) and event.get("operation") == "bindings":
            try:
                payload = event.get("payload") or {}
                action = payload.get("action")
                panel = self.plugin.panel
                if action == "list":
                    return {"ok": True, **panel.bootstrap()}
                if action == "save":
                    return await panel.save_binding(payload)
                if action == "delete":
                    return await panel.delete_binding(payload)
                raise ValueError("不支持的绑定操作")
            except (ValueError, TypeError, AttributeError) as exc:
                return {"ok": False, "error": str(exc)[:200]}
        if not self.plugin.config.get("context_sync_enabled", False):
            return {"ok": False, "error": "人格与记忆同步已关闭"}
        try:
            if not isinstance(event, dict) or event.get("operation") not in {"prepare", "recall", "remember", "commit"}:
                raise ValueError("不支持的桥接请求")
            group = self._binding(event.get("room"))
            scope = self._source(group)
            provider, adapter, memory_error = self._provider()
            operation = event["operation"]
            if operation == "prepare":
                scope["persona_id"], persona = await self._persona(scope)
                scope["memory_persona_id"], _ = await self._persona(scope, follow_group=True)
                memory_context = ""
                if adapter:
                    try:
                        recall = await asyncio.wait_for(adapter.recall(scope,
                            {"query": "本群最近的共同经历、约定和稳定偏好"}, "initial"), 1.5)
                        memory_context = recall.get("context", "") if recall.get("ok") is True else ""
                    except Exception:
                        memory_error = "初始记忆加载失败，可在对话中重新查询"
                return {"ok": True, **scope, "persona": persona, "provider": provider if adapter else "",
                        "memory_error": memory_error, "memory_context": memory_context}
            old = event.get("scope") or {}
            if any(old.get(key) != scope.get(key) for key in ("group_id", "umo", "platform_id")):
                raise ValueError("群绑定或平台来源已变化，请刷新语音上下文")
            if not adapter or old.get("provider") != provider:
                raise MemoryUnavailable(memory_error or "记忆插件已变化，请刷新语音上下文")
            scope["persona_id"] = old.get("persona_id", "")
            scope["memory_persona_id"] = old.get("memory_persona_id", scope["persona_id"])
            current_memory_persona, _ = await self._persona(scope, follow_group=True)
            if current_memory_persona != scope["memory_persona_id"]:
                raise ValueError("QQ 群人格已变化，请刷新语音上下文")
            payload = dict(event.get("payload") or {})
            payload.pop("qq_user_id", None)
            speaker = str(payload.get("speaker") or "")
            links = self.plugin.config.get("context_identity_links") or {}
            user = links.get(speaker, "") if speaker else ""
            if user:
                if not str(user).isdigit():
                    raise ValueError("成员关联必须填写 QQ 号")
                payload["qq_user_id"] = str(user)
            event_id = str(event.get("request_id") or "")
            if not event_id or len(event_id) > 100:
                raise ValueError("桥接请求缺少有效轮次 ID")
            if operation == "recall":
                return await adapter.recall(scope, payload, event_id)
            async with self._lock:
                saved = self.state.get("result:" + event_id)
                if saved is not None:
                    return saved
                if operation == "remember":
                    result = await adapter.remember(scope, payload, event_id)
                else:
                    def stage(role, done=False):
                        key = f"stage:{event_id}:{role}"
                        if done:
                            self.state.put(key, True)
                        return self.state.get(key) is True
                    result = await adapter.ingest(scope, payload, event_id, stage)
                self.state.put("result:" + event_id, result)
                return result
        except MemoryUnavailable as exc:
            return {"ok": False, "retry": event.get("operation") == "commit", "error": str(exc)[:200]}
        except (ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "error": str(exc)[:200]}

    async def _serve_event(self, event):
        try:
            result = await asyncio.wait_for(self.handle(event),
                18 if event.get("operation") == "bindings" else 10 if event.get("operation") == "commit" else 3)
        except Exception as exc:
            logger.warning("Ooptra memory operation failed: %s", type(exc).__name__)
            result = {"ok": False, "retry": event.get("operation") == "commit",
                      "error": "记忆插件执行超时或失败"}
        self.error = "" if result.get("ok") is True else result.get("error", "")
        self.last_operation = event.get("operation", "")
        try:
            await self.plugin.client.context_result(event["request_id"], result, self.client_id)
        except Exception as exc:
            self.error = f"桥接结果回传失败：{type(exc).__name__}"

    async def _worker(self):
        while True:
            try:
                events = await self.plugin.client.context_poll(self.client_id)
                for event in events:
                    if len(self._jobs) >= 8:
                        await asyncio.wait(self._jobs, return_when=asyncio.FIRST_COMPLETED)
                    job = asyncio.create_task(self._serve_event(event))
                    self._jobs.add(job)
                    job.add_done_callback(self._jobs.discard)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error = f"桥接连接失败：{type(exc).__name__}"
                await asyncio.sleep(2)

"""Adapters for explicitly supported memory providers; no database scraping."""

from __future__ import annotations

import importlib
from copy import copy
from typing import Any


class MemoryUnavailable(ValueError):
    pass


class _CapturedAssistant:
    """An isolated reflection view: the assistant row already has a durable receipt."""
    def __init__(self, conversations, content):
        self.conversations, self.content = conversations, content

    def __getattr__(self, name):
        return getattr(self.conversations, name)

    async def add_message_from_event(self, *, event, role, content):
        if role != "assistant" or content != self.content:
            raise MemoryUnavailable("LivingMemory 反思接口发生变化")
        # Reflection ignores the return value; it must not insert this row twice.
        return None


def voice_event(scope: dict, payload: dict, event_id: str):
    """Construct a source event without putting it through chat commands or sending QQ messages."""
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.message_components import Plain
    from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata

    class VoiceSourceEvent(AstrMessageEvent):
        async def send(self, message):
            raise MemoryUnavailable("语音来源事件不能向 QQ 群发送消息")

    private = scope.get("message_type") == "FriendMessage"
    sender = (scope.get("target_user_id") if private else None) or payload.get("qq_user_id") or "oopz:" + (payload.get("speaker") or "mixed")
    text = str(payload.get("user_text") or payload.get("query") or payload.get("content") or "")
    msg = AstrBotMessage()
    msg.type = MessageType.FRIEND_MESSAGE if private else MessageType.GROUP_MESSAGE
    msg.self_id = scope.get("bot_id", "")
    msg.group_id = scope["group_id"]
    msg.session_id = scope.get("session_id", scope["group_id"])
    msg.sender = MessageMember(user_id=sender, nickname=sender)
    msg.message_id = "ooptra:" + event_id
    msg.message = [Plain(text=text)]
    msg.message_str = text
    msg.raw_message = {"source": "ooptra_voice", "oopz_user_id": payload.get("speaker", "")}
    event = VoiceSourceEvent(text, msg, PlatformMetadata(
        name=scope.get("platform_name", "aiocqhttp"), description="Ooptra voice source", id=scope["platform_id"]),
        scope.get("session_id", scope["group_id"]))
    event.unified_msg_origin = scope["umo"]
    event.set_extra("_trace_persona_id", scope.get("memory_persona_id", scope.get("persona_id")))
    event.set_extra("ooptra_voice_source", True)
    return event


class CompanionMemory:
    def __init__(self, metadata):
        self.instance = metadata.star_cls
        module = getattr(metadata, "module", None)
        getter = getattr(module, "get_memory_companion_bridge", None)
        if callable(getter) and getter() is None:
            raise MemoryUnavailable("Memory Companion 已关闭对外 Bridge")
        self.bridge = getattr(self.instance, "memory_companion", None)
        if self.bridge is None:
            raise MemoryUnavailable("Memory Companion Bridge 未就绪")
        lifecycle = getattr(self.bridge, "bridge_lifecycle_status", None)
        if callable(lifecycle) and lifecycle().get("active") is not True:
            raise MemoryUnavailable("Memory Companion Bridge 已停用")
        for name in ("compose_context", "record_visible_turn", "remember"):
            if not callable(getattr(self.bridge, name, None)):
                raise MemoryUnavailable(f"Memory Companion 缺少接口 {name}")

    async def recall(self, scope: dict, payload: dict, event_id: str) -> dict:
        probe = getattr(self.bridge, "p5_capability_status", None)
        if callable(probe) and probe().get("bridge_gate"):
            raise MemoryUnavailable("Memory Companion 已启用授权校验，当前语音桥接尚未获授权")
        # Empty reader identity and strict group session prevent private-person recall.
        private = scope.get("message_type") == "FriendMessage"
        ctx = {"session_id": scope["umo"], "scope": "private" if private else "group", "platform": scope.get("platform_name", "aiocqhttp"),
               "group_id": scope["group_id"], "bot_id": scope.get("bot_id", ""),
               "persona_id": scope.get("memory_persona_id", scope.get("persona_id", "")),
               "user_id": scope.get("target_user_id", "") if private else "",
               "strict_session_only": True}
        text = await self.bridge.compose_context(query=payload["query"], session_context=ctx,
                                                  top_k=5, max_chars=2500)
        return {"ok": True, "context": str(text or "")[:6000]}

    async def remember(self, scope: dict, payload: dict, event_id: str) -> dict:
        event = voice_event(scope, payload, event_id)
        result = await self.bridge.remember(event=event, content=payload["content"])
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise MemoryUnavailable("Memory Companion 未确认长期记忆写入成功")
        return {"ok": True, "state": "stored"}

    async def ingest(self, scope: dict, payload: dict, event_id: str, stage) -> dict:
        for role, key in (("user", "user_text"), ("assistant", "reply_text")):
            if not payload.get(key) or stage(role):
                continue
            result = await self.bridge.record_visible_turn(
                role=role, content=payload[key], scope="private" if scope.get("message_type") == "FriendMessage" else "group", session_id=scope["umo"],
                platform=scope.get("platform_name", "aiocqhttp"), user_id=scope.get("target_user_id") or payload.get("qq_user_id") or
                "oopz:" + (payload.get("speaker") or "mixed"),
                group_id=scope["group_id"], message_id=f"ooptra:{event_id}:{role}",
                source="ooptra_voice", metadata={"bot_id": scope.get("bot_id", ""),
                    "persona_id": scope.get("memory_persona_id", scope.get("persona_id", "")),
                    "interrupted": payload.get("interrupted", False),
                    "oopz_speaker": payload.get("speaker", ""),
                    "delivery": "submitted" if role == "assistant" else "observed"})
            if not result:
                raise MemoryUnavailable("Memory Companion 未接收语音记录，可能已关闭该群采集")
            stage(role, True)
        return {"ok": True, "state": "captured"}


class LivingMemory:
    def __init__(self, metadata):
        self.instance = metadata.star_cls
        self.engine = getattr(self.instance, "memory_engine", None)
        self.handler = getattr(self.instance, "event_handler", None)
        self.conversations = getattr(self.instance, "conversation_manager", None)
        self.message_utils = getattr(self.handler, "_message_utils", None)
        self.reflection = getattr(self.handler, "_memory_reflection", None)
        self.config = getattr(self.instance, "config_manager", None)
        module = metadata.module_path or type(self.instance).__module__
        root = module.rsplit(".", 1)[0]
        self.policy = importlib.import_module(root + ".core.memory_scope")
        if not (callable(getattr(self.engine, "search_memories", None))
                and callable(getattr(self.handler, "handle_all_group_messages", None))
                and callable(getattr(self.handler, "handle_memory_reflection", None))
                and callable(getattr(self.conversations, "add_message_from_event", None))
                and callable(getattr(self.conversations, "get_context", None))
                and callable(getattr(self.message_utils, "enforce_message_limit", None))
                and callable(getattr(self.reflection, "handle_memory_reflection", None))):
            raise MemoryUnavailable("LivingMemory 运行接口未就绪或不兼容")

    def _allowed(self, event):
        if not self.policy.is_event_memory_allowed(self.config, event):
            raise MemoryUnavailable("LivingMemory 白名单不允许当前语音来源")

    async def recall(self, scope: dict, payload: dict, event_id: str) -> dict:
        self._allowed(voice_event(scope, payload, event_id))
        rows = await self.engine.search_memories(query=payload["query"], k=5,
            session_id=scope["umo"], persona_id=scope.get("memory_persona_id", scope.get("persona_id")) or None)
        # Always use the bound group scope, even when a provider permits global recall.
        return {"ok": True, "context": "\n".join(str(row.content)[:1200] for row in rows[:5])[:6000]}

    async def remember(self, scope: dict, payload: dict, event_id: str) -> dict:
        self._allowed(voice_event(scope, payload, event_id))
        processor = getattr(self.instance, "memory_processor", None)
        if not callable(getattr(processor, "build_memory_from_structured_data", None)):
            raise MemoryUnavailable("LivingMemory 不支持当前长期记忆写入接口")
        content, metadata, importance = processor.build_memory_from_structured_data(
            structured_data={"summary": payload["content"], "topics": [],
                             "key_facts": [], "sentiment": "neutral", "importance": 0.7},
            is_group_chat=True, fallback_excerpt=payload["content"])
        metadata.update({"memory_origin": "ooptra_voice", "source_session_id": scope["umo"],
                         "source_event_id": event_id})
        result = await self.engine.add_memory(content=content, session_id=scope["umo"],
            persona_id=scope.get("memory_persona_id", scope.get("persona_id")) or None,
            importance=importance, metadata=metadata)
        if not result:
            raise MemoryUnavailable("LivingMemory 未确认长期记忆写入成功")
        return {"ok": True, "state": "stored"}

    async def ingest(self, scope: dict, payload: dict, event_id: str, stage) -> dict:
        event = voice_event(scope, payload, event_id)
        self._allowed(event)
        if not self.config.get("session_manager.enable_full_group_capture", True):
            raise MemoryUnavailable("LivingMemory 已关闭群消息采集")
        if payload.get("user_text") and not stage("user"):
            # The passive capture handler swallows errors. Use its public storage
            # operation after the same whitelist/capture checks and require a receipt.
            stored = await self.conversations.add_message_from_event(
                event=event, role="user", content=payload["user_text"])
            if not getattr(stored, "id", None):
                raise MemoryUnavailable("LivingMemory 未确认用户语音记录已保存")
            stage("user", True)
        await self.message_utils.enforce_message_limit(scope["umo"])
        if payload.get("reply_text") and not stage("assistant"):
            # Preserve the current provider's intentional error-response filter.
            indicators = ("api error", "request failed", "rate limit", "timeout", "connection error",
                          "服务暂时不可用", "请求失败", "接口错误")
            if any(word in payload["reply_text"].lower() for word in indicators):
                stage("assistant", True)
                return {"ok": True, "state": "captured_filtered"}
            stored = await self.conversations.add_message_from_event(
                event=event, role="assistant", content=payload["reply_text"])
            if not getattr(stored, "id", None):
                raise MemoryUnavailable("LivingMemory 未确认助手语音记录已保存")
            stage("assistant", True)
        if payload.get("reply_text") and not stage("reflection"):
            from astrbot.api.provider import LLMResponse
            reflection = copy(self.reflection)
            reflection.conversation_manager = _CapturedAssistant(self.conversations, payload["reply_text"])
            # Share the provider's task tracking/locks, but never mutate its live
            # ConversationManager or reinsert a previously confirmed assistant row.
            await reflection.handle_memory_reflection(event,
                LLMResponse(role="assistant", completion_text=payload["reply_text"]))
            stage("reflection", True)
        await self.message_utils.enforce_message_limit(scope["umo"])
        return {"ok": True, "state": "captured"}


ADAPTERS = {"memory_companion": CompanionMemory, "livingmemory": LivingMemory}


def discover(context) -> list[dict[str, Any]]:
    result = []
    getter = getattr(context, "get_all_stars", None)
    for metadata in getter() if callable(getter) else []:
        if not getattr(metadata, "activated", False) or getattr(metadata, "star_cls", None) is None:
            continue
        root = str(getattr(metadata, "root_dir_name", "") or "")
        for name, adapter in ADAPTERS.items():
            if root != "astrbot_plugin_" + name:
                continue
            row = {"id": name, "name": str(metadata.name or name),
                   "version": str(metadata.version or ""), "available": False}
            try:
                row["adapter"] = adapter(metadata)
                row["available"] = True
            except (MemoryUnavailable, ImportError, AttributeError) as exc:
                row["error"] = str(exc)
            result.append(row)
    return result

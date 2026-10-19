from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_main_logic import _main

_main()
from ooptra_plugin_under_test.voice_memory import (
    CompanionMemory,
    LivingMemory,
    MemoryUnavailable,
)

SCOPE = {"group_id": "123", "umo": "qq:GroupMessage:123", "platform_id": "qq",
         "persona_id": "voice-persona", "memory_persona_id": "group-persona", "bot_id": "42"}


class MemoryAdapterTests(unittest.IsolatedAsyncioTestCase):
    def companion(self):
        bridge = types.SimpleNamespace(compose_context=AsyncMock(return_value="public memory"),
            record_visible_turn=AsyncMock(return_value="timeline-id"),
            remember=AsyncMock(return_value={"ok": True}))
        return CompanionMemory(types.SimpleNamespace(star_cls=types.SimpleNamespace(memory_companion=bridge)))

    async def test_companion_queries_only_bound_group_without_private_identity(self):
        adapter = self.companion()
        result = await adapter.recall(SCOPE, {"query": "our plans", "qq_user_id": "999"}, "turn")
        self.assertEqual(result["context"], "public memory")
        ctx = adapter.bridge.compose_context.call_args.kwargs["session_context"]
        self.assertEqual(ctx["session_id"], SCOPE["umo"])
        self.assertEqual(ctx["user_id"], "")
        self.assertTrue(ctx["strict_session_only"])
        self.assertEqual(ctx["persona_id"], "group-persona")

    async def test_companion_does_not_bypass_authorization_gate(self):
        adapter = self.companion()
        adapter.bridge.p5_capability_status = lambda: {"bridge_gate": True}
        with self.assertRaises(MemoryUnavailable):
            await adapter.recall(SCOPE, {"query": "secret"}, "turn")
        adapter.bridge.compose_context.assert_not_awaited()

    async def test_private_companion_reads_and_records_the_selected_private_scope(self):
        adapter = self.companion()
        scope = {**SCOPE, "umo": "qq:FriendMessage:456", "message_type": "FriendMessage",
                 "target_user_id": "456", "group_id": "", "session_id": "456"}
        await adapter.recall(scope, {"query": "fact"}, "turn")
        ctx = adapter.bridge.compose_context.call_args.kwargs["session_context"]
        self.assertEqual(ctx["user_id"], "456")
        self.assertEqual(ctx["session_id"], scope["umo"])
        await adapter.ingest(scope, {"user_text": "question", "speaker": "oopz-user"}, "private", lambda *args: False)
        call = adapter.bridge.record_visible_turn.call_args.kwargs
        self.assertEqual(call["scope"], "private")
        self.assertEqual(call["user_id"], "456")

    async def test_companion_checkpoints_roles_and_keeps_unknown_user_namespace(self):
        adapter = self.companion()
        done = set()

        def stage(role, value=False):
            if value:
                done.add(role)
            return role in done

        payload = {"user_text": "question", "reply_text": "answer", "speaker": "uid"}
        await adapter.ingest(SCOPE, payload, "event", stage)
        await adapter.ingest(SCOPE, payload, "event", stage)
        self.assertEqual(adapter.bridge.record_visible_turn.await_count, 2)
        first = adapter.bridge.record_visible_turn.call_args_list[0].kwargs
        self.assertEqual(first["user_id"], "oopz:uid")
        self.assertEqual(first["scope"], "group")

    async def test_companion_never_claims_failed_write_succeeded(self):
        adapter = self.companion()
        adapter.bridge.remember.return_value = {"ok": False}
        with patch("ooptra_plugin_under_test.voice_memory.voice_event", return_value=object()):
            with self.assertRaises(MemoryUnavailable):
                await adapter.remember(SCOPE, {"content": "fact"}, "event")

    def living(self):
        engine = types.SimpleNamespace(search_memories=AsyncMock(return_value=[
            types.SimpleNamespace(content="group fact")]), add_memory=AsyncMock(return_value=12))
        handler = types.SimpleNamespace(handle_all_group_messages=AsyncMock(),
            handle_memory_reflection=AsyncMock(),
            _memory_reflection=types.SimpleNamespace(handle_memory_reflection=AsyncMock()),
            _message_utils=types.SimpleNamespace(enforce_message_limit=AsyncMock()))
        conversations = types.SimpleNamespace(add_message_from_event=AsyncMock(
            return_value=types.SimpleNamespace(id=10)), get_context=AsyncMock(return_value=[
                {"role": "assistant", "content": "answer"}]),
            store=types.SimpleNamespace(get_message_count=AsyncMock(side_effect=[1, 2])))
        instance = types.SimpleNamespace(memory_engine=engine, event_handler=handler,
            conversation_manager=conversations,
            config_manager={}, memory_processor=types.SimpleNamespace(
                build_memory_from_structured_data=lambda **kw: (kw["fallback_excerpt"], {}, 0.7)))
        metadata = types.SimpleNamespace(star_cls=instance, module_path="installed.living.main")
        policy = types.SimpleNamespace(is_event_memory_allowed=lambda *args: True)
        with patch("importlib.import_module", return_value=policy):
            return LivingMemory(metadata)

    async def test_livingmemory_uses_group_persona_for_both_read_and_explicit_write(self):
        adapter = self.living()
        with patch("ooptra_plugin_under_test.voice_memory.voice_event", return_value=object()):
            await adapter.recall(SCOPE, {"query": "fact"}, "r")
            await adapter.remember(SCOPE, {"content": "fact"}, "w")
        for call in (adapter.engine.search_memories.call_args, adapter.engine.add_memory.call_args):
            self.assertEqual(call.kwargs["session_id"], SCOPE["umo"])
            self.assertEqual(call.kwargs["persona_id"], "group-persona")

    async def test_livingmemory_checks_whitelist_before_query(self):
        adapter = self.living()
        adapter.policy.is_event_memory_allowed = lambda *args: False
        with patch("ooptra_plugin_under_test.voice_memory.voice_event", return_value=object()):
            with self.assertRaises(MemoryUnavailable):
                await adapter.recall(SCOPE, {"query": "secret"}, "r")
        adapter.engine.search_memories.assert_not_awaited()

    async def test_livingmemory_captures_user_and_runs_reflection(self):
        adapter = self.living()
        done = set()

        def stage(role, value=False):
            if value:
                done.add(role)
            return role in done

        provider = types.ModuleType("astrbot.api.provider")
        provider.LLMResponse = lambda **kw: types.SimpleNamespace(**kw)
        with patch.dict(sys.modules, {"astrbot.api.provider": provider}), patch(
                "ooptra_plugin_under_test.voice_memory.voice_event", return_value=object()):
            await adapter.ingest(SCOPE, {"user_text": "question", "reply_text": "answer"}, "event", stage)
        self.assertEqual(adapter.conversations.add_message_from_event.await_count, 2)
        adapter.reflection.handle_memory_reflection.assert_awaited_once()
        self.assertEqual(adapter.message_utils.enforce_message_limit.await_count, 2)

    async def test_livingmemory_requires_storage_receipt(self):
        adapter = self.living()
        adapter.conversations.add_message_from_event.return_value = None
        provider = types.ModuleType("astrbot.api.provider")
        provider.LLMResponse = lambda **kw: types.SimpleNamespace(**kw)
        with patch.dict(sys.modules, {"astrbot.api.provider": provider}), patch(
                "ooptra_plugin_under_test.voice_memory.voice_event", return_value=object()):
            with self.assertRaises(MemoryUnavailable):
                await adapter.ingest(SCOPE, {"reply_text": "answer"}, "event", lambda *args: False)

    async def test_reflection_proxy_skips_duplicate_insert_and_preserves_native_manager(self):
        adapter = self.living()

        class NativeReflection:
            def __init__(self, conversations):
                self.conversation_manager = conversations
                self.tasks = set()

            async def handle_memory_reflection(self, event, response):
                await self.conversation_manager.add_message_from_event(
                    event=event, role="assistant", content=response.completion_text)
                await self.conversation_manager.get_context("scope")
                self.tasks.add("scheduled")

        native = NativeReflection(adapter.conversations)
        adapter.reflection = native
        provider = types.ModuleType("astrbot.api.provider")
        provider.LLMResponse = lambda **kw: types.SimpleNamespace(**kw)
        with patch.dict(sys.modules, {"astrbot.api.provider": provider}), patch(
                "ooptra_plugin_under_test.voice_memory.voice_event", return_value=object()):
            await adapter.ingest(SCOPE, {"user_text": "question", "reply_text": "answer"}, "event", lambda *args: False)
        self.assertEqual(adapter.conversations.add_message_from_event.await_count, 2)
        self.assertIs(native.conversation_manager, adapter.conversations)
        self.assertEqual(native.tasks, {"scheduled"})
        adapter.conversations.get_context.assert_awaited_once_with("scope")

    async def test_livingmemory_intentional_filter_does_not_poison_retry_queue(self):
        adapter = self.living()
        with patch("ooptra_plugin_under_test.voice_memory.voice_event", return_value=object()):
            result = await adapter.ingest(SCOPE, {"reply_text": "timeout 后重试"}, "event", lambda *args: False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "captured_filtered")
        adapter.conversations.add_message_from_event.assert_not_awaited()

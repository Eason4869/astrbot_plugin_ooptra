from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_main_logic import FakeConfig, _main


class ContextSyncTests(unittest.IsolatedAsyncioTestCase):
    def service(self, **config):
        _main()
        from ooptra_plugin_under_test.context_sync import ContextSync

        platform = types.SimpleNamespace(meta=lambda: types.SimpleNamespace(id="qq1", name="aiocqhttp"))
        persona_manager = types.SimpleNamespace(
            personas_v3=[{"name": "p", "prompt": "selected persona"}],
        )

        async def resolve(**kwargs):
            return "p", persona_manager.personas_v3[0], None, False

        persona_manager.resolve_selected_persona = resolve
        context = types.SimpleNamespace(
            platform_manager=types.SimpleNamespace(get_insts=lambda: [platform]),
            persona_manager=persona_manager, get_all_stars=lambda: [],
        )
        plugin = types.SimpleNamespace(context=context, config=FakeConfig(
            context_sync_enabled=True, group_map={"123": {"area": "a", "channel": "c"}}, **config))
        return ContextSync(plugin)

    async def test_prepare_reuses_group_binding_and_resolves_persona(self):
        service = self.service()
        result = await service.handle({"operation": "prepare", "room": {"area": "a", "channel": "c"}})
        self.assertTrue(result["ok"])
        self.assertEqual(result["umo"], "qq1:GroupMessage:123")
        self.assertEqual(result["persona"], "selected persona")
        self.assertEqual(result["provider"], "")

    async def test_reverse_binding_ambiguity_is_visible(self):
        service = self.service()
        service.plugin.config["group_map"]["456"] = {"area": "a", "channel": "c"}
        result = await service.handle({"operation": "prepare", "room": {"area": "a", "channel": "c"}})
        self.assertFalse(result["ok"])
        self.assertIn("多个", result["error"])

    async def test_explicit_source_is_independent_of_multiple_control_groups(self):
        service = self.service(context_sources={"rules": [{"area": "a", "channel": "",
            "persona_umo": "qq1:GroupMessage:456", "memory_umos": ["qq1:GroupMessage:123"]}]})
        service.plugin.config["group_map"]["456"] = {"area": "a"}
        service.plugin.config["group_map"]["789"] = {"area": "a", "channel": "c"}
        result = await service.handle({"operation": "prepare", "room": {"area": "a", "channel": "c"}})
        self.assertTrue(result["ok"])
        self.assertEqual(result["umo"], "qq1:GroupMessage:456")

    async def test_room_source_overrides_domain_default_and_empty_explicit_rules_disable_fallback(self):
        service = self.service(context_sources={"rules": [
            {"area": "a", "channel": "", "persona_umo": "qq1:GroupMessage:123"},
            {"area": "a", "channel": "c", "persona_umo": "qq1:GroupMessage:456"}]})
        service.plugin.config["group_map"]["456"] = {"area": "a"}
        result = await service.handle({"operation": "prepare", "room": {"area": "a", "channel": "c"}})
        self.assertEqual(result["group_id"], "456")
        service.plugin.config["context_sources"] = {"rules": []}
        self.assertFalse((await service.handle({"operation": "prepare", "room": {"area": "a", "channel": "c"}}))["ok"])

    async def test_same_group_number_on_different_platforms_uses_selected_session(self):
        service = self.service(context_sources={"rules": [{"area": "a", "channel": "c",
            "persona_umo": "oopz-ws:GroupMessage:123", "memory_umos": []}]})
        service.plugin.config["group_map"]["oopz-ws:GroupMessage:123"] = {"area": "a", "kind": "oopz"}
        # Explicit instances avoid the shared aiocqhttp adapter name deciding identity.
        service.plugin.context.platform_manager.get_insts = lambda: [
            types.SimpleNamespace(meta=lambda: types.SimpleNamespace(id="qq1", name="aiocqhttp")),
            types.SimpleNamespace(meta=lambda: types.SimpleNamespace(id="oopz-ws", name="aiocqhttp"))]
        result = await service.handle({"operation": "prepare", "room": {"area": "a", "channel": "c"}})
        self.assertTrue(result["ok"])
        self.assertEqual(result["umo"], "oopz-ws:GroupMessage:123")
        self.assertEqual(result["source_kind"], "oopz")

    async def test_legacy_integer_alias_is_the_same_qq_group(self):
        service = self.service()
        service.plugin.config["group_map"][123] = {"area": "a", "channel": "c"}
        result = await service.handle({"operation": "prepare", "room": {"area": "a", "channel": "c"}})
        self.assertTrue(result["ok"])

    def mapped_service(self, private=False):
        umos = ["qq1:GroupMessage:123", "qq1:FriendMessage:456" if private else "qq1:GroupMessage:456"]
        service = self.service(context_persona_id="deleted-global", context_sources={"rules": [
            {"area": "a", "channel": "c", "persona_umo": umos[0], "memory_umos": umos, "allow_private": private}]})
        service.plugin.config["group_map"] = {umo: {"area": "a", "channel": "c"} for umo in umos}
        adapter = types.SimpleNamespace(recall=AsyncMock(return_value={"ok": True, "context": "fact"}),
            remember=AsyncMock(return_value={"ok": True, "state": "stored"}),
            ingest=AsyncMock(return_value={"ok": True, "state": "captured"}))
        service._provider = lambda: ("test", adapter, "")
        return service, adapter, umos

    async def test_read_and_write_all_selected_sessions_with_partial_retry(self):
        service, adapter, umos = self.mapped_service()
        room = {"area": "a", "channel": "c"}
        prepared = await service.handle({"operation": "prepare", "room": room})
        self.assertTrue(prepared["ok"], prepared)
        self.assertEqual(prepared["persona_id"], "p")  # Explicit source ignores legacy global override.
        self.assertEqual([c.args[0]["umo"] for c in adapter.recall.await_args_list], umos)
        adapter.ingest.side_effect = [{"ok": True}, RuntimeError("offline"), {"ok": True}]
        event = {"operation": "commit", "room": room, "scope": prepared,
                 "request_id": "fanout-turn", "payload": {"user_text": "shared fact"}}
        partial = await service.handle(event)
        self.assertFalse(partial["ok"])
        self.assertTrue(partial["retry"])
        self.assertEqual([t["ok"] for t in partial["targets"]], [True, False])
        complete = await service.handle(event)
        self.assertTrue(complete["ok"], complete)
        self.assertEqual([c.args[0]["umo"] for c in adapter.ingest.await_args_list], [*umos, umos[1]])
        self.assertEqual(adapter.ingest.await_args_list[1].args[2], adapter.ingest.await_args_list[2].args[2])
        await service.handle(event)
        self.assertEqual(adapter.ingest.await_count, 3)

    async def test_remember_fans_out_and_route_change_rejects_old_turn(self):
        service, adapter, umos = self.mapped_service()
        room = {"area": "a", "channel": "c"}
        prepared = await service.handle({"operation": "prepare", "room": room})
        event = {"operation": "remember", "room": room, "scope": prepared,
                 "request_id": "remember-all", "payload": {"content": "fact"}}
        self.assertTrue((await service.handle(event))["ok"])
        self.assertEqual([c.args[0]["umo"] for c in adapter.remember.await_args_list], umos)
        service.plugin.config["context_sources"]["rules"][0]["memory_umos"] = [umos[1]]
        self.assertFalse((await service.handle({**event, "request_id": "stale"}))["ok"])
        self.assertEqual(adapter.remember.await_count, 2)

    async def test_private_memory_requires_explicit_consent_and_retains_exact_umo(self):
        service, adapter, umos = self.mapped_service(private=True)
        room = {"area": "a", "channel": "c"}
        service.plugin.config["context_sources"]["rules"][0]["allow_private"] = False
        self.assertFalse((await service.handle({"operation": "prepare", "room": room}))["ok"])
        adapter.recall.assert_not_awaited()
        service.plugin.config["context_sources"]["rules"][0]["allow_private"] = True
        result = await service.handle({"operation": "prepare", "room": room})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["memory_scopes"][1]["umo"], umos[1])
        self.assertEqual(result["memory_scopes"][1]["target_user_id"], "456")
        self.assertEqual(result["memory_scopes"][1]["group_id"], "")

    async def test_new_umo_mapping_cannot_activate_private_memory_through_legacy_fallback(self):
        service, adapter, umos = self.mapped_service(private=True)
        service.plugin.config.pop("context_sources")
        service.plugin.config["group_map"] = {umos[1]: {"area": "a", "allow_control": False}}
        result = await service.handle({"operation": "prepare", "room": {"area": "a", "channel": "c"}})
        self.assertFalse(result["ok"])
        adapter.recall.assert_not_awaited()

    async def test_catalog_uses_platform_identity_and_validates_oopz_ws_login(self):
        service = self.service()
        actions = AsyncMock(side_effect=[{"user_id": 42}, [{"user_id": 456, "nickname": "Friend"}], {"user_id": 42}])
        service.plugin.context.platform_manager.get_insts = lambda: [types.SimpleNamespace(
            meta=lambda: types.SimpleNamespace(id="qq1", name="aiocqhttp"),
            get_client=lambda: types.SimpleNamespace(call_action=actions))]
        rows = await service.source_options("qq1", "friend")
        self.assertEqual(rows["groups"][0]["umo"], "qq1:FriendMessage:456")
        service.plugin.client = types.SimpleNamespace(text_channels=AsyncMock(return_value={
            "bot_id": "other-bot", "channels": [{"group_id": "123", "name": "text", "id": "t"}]}))
        with self.assertRaisesRegex(ValueError, "WS"):
            await service.source_options("qq1", "oopz", "a")

    async def test_memory_limit_duplicate_umo_and_control_characters_are_rejected(self):
        service, adapter, umos = self.mapped_service()
        rule = service.plugin.config["context_sources"]["rules"][0]
        for targets in ([umos[0], umos[0]], ["qq1:GroupMessage:" + str(n) for n in range(9)], ["qq1:GroupMessage:12\x003"]):
            rule["memory_umos"] = targets
            result = await service.handle({"operation": "prepare", "room": {"area": "a", "channel": "c"}})
            self.assertFalse(result["ok"], targets)
        adapter.recall.assert_not_awaited()

    async def test_disabled_or_unbound_never_exports_persona(self):
        service = self.service()
        service.plugin.config["context_sync_enabled"] = False
        result = await service.handle({"operation": "prepare", "room": {"area": "a", "channel": "c"}})
        self.assertFalse(result["ok"])
        self.assertNotIn("persona", result)

    async def test_old_group_scope_cannot_be_redirected_after_rebinding(self):
        service = self.service()
        result = await service.handle({"operation": "recall", "room": {"area": "a", "channel": "c"},
                                       "scope": {"group_id": "456"}, "payload": {"query": "secret"}})
        self.assertFalse(result["ok"])

    async def test_multiple_memory_plugins_require_explicit_selection(self):
        service = self.service()
        instance = types.SimpleNamespace(memory_companion=types.SimpleNamespace())
        service.plugin.context.get_all_stars = lambda: [
            types.SimpleNamespace(root_dir_name="astrbot_plugin_memory_companion", activated=True,
                                  star_cls=instance, name="MemoryCompanion", version="2.3.1"),
            types.SimpleNamespace(root_dir_name="astrbot_plugin_livingmemory", activated=True,
                                  star_cls=types.SimpleNamespace(), name="LivingMemory", version="1"),
        ]
        status = service.describe()
        self.assertEqual(len(status["providers"]), 2)
        self.assertIn("选择", status["memory_error"])

    async def test_worker_shutdown_cancels_long_poll(self):
        service = self.service()
        began = asyncio.Event()

        async def poll(client_id):
            began.set()
            await asyncio.Event().wait()

        service.plugin.client = types.SimpleNamespace(context_poll=poll)
        service.start()
        await began.wait()
        await service.stop()
        self.assertIsNone(service.task)

    async def test_bindings_can_be_managed_when_persona_sync_is_disabled(self):
        service = self.service()
        service.plugin.config["context_sync_enabled"] = False
        service.plugin.panel = types.SimpleNamespace(bootstrap=lambda: {"bindings": []})
        result = await service.handle({"operation": "bindings", "payload": {"action": "list"}})
        self.assertTrue(result["ok"])
        self.assertEqual(result["bindings"], [])

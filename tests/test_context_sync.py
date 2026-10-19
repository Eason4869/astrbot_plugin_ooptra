from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path

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

    async def test_legacy_integer_alias_is_the_same_qq_group(self):
        service = self.service()
        service.plugin.config["group_map"][123] = {"area": "a", "channel": "c"}
        result = await service.handle({"operation": "prepare", "room": {"area": "a", "channel": "c"}})
        self.assertTrue(result["ok"])

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

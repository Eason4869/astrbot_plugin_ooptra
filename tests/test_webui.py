"""Plugin control panel: persistence, domain boundaries, and real HTTP contracts."""

from __future__ import annotations

import asyncio
import json
import unittest
from copy import deepcopy

import httpx
from test_main_logic import _plugin

from ooptra_client import OoptraClient


class TestControlPanel(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin, self.config = _plugin(api_token="server-only-secret")
        self.panel = getattr(self.plugin, "panel", None)
        self.assertIsNotNone(self.panel, "The AstrBot control panel is not implemented")
        self.requests = []
        self.responses = {
            "/voice/status": {"ok": True, "joined": False, "backend": "gemini_live",
                              "default_area": "AREA-A", "default_channel": "CHANNEL-A"},
            "/oopz/areas": {"ok": True, "areas": [{"id": "AREA-A", "name": "游戏域"}]},
            "/voice/channels": {"ok": True, "channels": [{"id": "CHANNEL-A", "name": "开黑房"}]},
            "/voice/members": {"ok": True, "count": 1, "members": [{"uid": "u1", "name": "成员", "mic": None, "speaker": False}]},
            "/voice/join": {"ok": True, "joined": True},
            "/voice/leave": {"ok": True, "joined": False},
            "/api/config": {"ok": True, "changed": {"voice": ["backend"]}, "hot_reloaded_fields": ["backend"], "notes": []},
            "/voice/auto-visit/config": {"ok": True, "changed": {"auto_visit": ["areas"]},
                "config": {"areas": {"AREA-A": {"enabled": True}}},
                "status": {"areas": {"AREA-A": {"enabled": True}}, "paused": True, "pause_reason": "正在手动会话"}, "notes": []},
        }

        def handle(request):
            self.requests.append(request)
            body = self.responses.get(request.url.path)
            return httpx.Response(200 if body is not None else 404, json=body or {"ok": False})

        self.plugin._client = OoptraClient(self.plugin._api_base(), token="server-only-secret", transport=httpx.MockTransport(handle))

    async def test_bootstrap_works_offline_and_never_exposes_token(self):
        self.config["group_map"] = {"123456": {"areas": ["AREA-A", "AREA-B"], "channel": "CHANNEL-A", "label": "游戏群"}}
        result = self.panel.bootstrap()
        self.assertEqual(result["bindings"][0]["areas"], ["AREA-A", "AREA-B"])
        self.assertNotIn("server-only-secret", json.dumps(result))
        self.assertEqual(self.requests, [])

    async def test_binding_edit_preserves_other_groups_and_multi_area_fields(self):
        self.config["group_map"] = {"123456": {"areas": ["AREA-A", "AREA-B"], "channel": "", "extra": "keep"},
                                     "654321": "AREA-C:CHANNEL-C"}
        await self.panel.save_binding({"group_id": "123456", "areas": ["AREA-A", "AREA-B"], "channel": "CHANNEL-A", "label": "新备注"})
        self.assertEqual(self.config["group_map"]["123456"], {"areas": ["AREA-A", "AREA-B"], "channel": "CHANNEL-A", "label": "新备注", "extra": "keep"})
        self.assertEqual(self.config["group_map"]["654321"], "AREA-C:CHANNEL-C")
        self.assertEqual(self.config.saved, 1)

    async def test_invalid_binding_does_not_modify_configuration(self):
        bodies = [None, [], {}, {"group_id": "oops", "areas": ["AREA-A"]},
                  {"group_id": "123456", "areas": []}, {"group_id": "123456", "areas": ["中文备注"]},
                  {"group_id": "123456", "areas": ["AREA-A"], "channel": "../escape"}]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(ValueError):
                await self.panel.save_binding(body)
        self.assertEqual(self.config["group_map"], {})
        self.assertEqual(self.config.saved, 0)

    async def test_unbind_removes_legacy_integer_key_only_for_target_group(self):
        self.config["group_map"] = {123456: "AREA-A:CHANNEL-A", "654321": "AREA-B:CHANNEL-B"}
        await self.panel.delete_binding({"group_id": "123456"})
        self.assertEqual(self.config["group_map"], {"654321": "AREA-B:CHANNEL-B"})

    async def test_failed_save_restores_in_memory_binding(self):
        self.config["group_map"] = {"123456": {"area": "AREA-A", "channel": "old"}}
        before = deepcopy(self.config["group_map"])

        def fail():
            raise OSError("disk full")

        self.config.save_config = fail
        with self.assertRaises(OSError):
            await self.panel.save_binding({"group_id": "123456", "areas": ["AREA-B"], "channel": "new"})
        self.assertEqual(self.config["group_map"], before)

    async def test_concurrent_binding_updates_preserve_both_groups(self):
        await asyncio.gather(*(self.panel.save_binding({"group_id": group, "areas": [area]})
                               for group, area in (("123456", "AREA-A"), ("654321", "AREA-B"))))
        self.assertEqual(set(self.config["group_map"]), {"123456", "654321"})

    async def test_reads_forward_bearer_on_server_and_validate_target_ids(self):
        self.assertEqual((await self.panel.areas())["areas"][0]["id"], "AREA-A")
        self.assertEqual((await self.panel.members("AREA-A", "CHANNEL-A"))["members"][0]["mic"], None)
        self.assertTrue(all(r.headers["Authorization"] == "Bearer server-only-secret" for r in self.requests))
        self.assertEqual(dict(self.requests[-1].url.params), {"area": "AREA-A", "channel": "CHANNEL-A"})
        with self.assertRaises(ValueError):
            await self.panel.channels("../api")

    async def test_join_verifies_channel_in_selected_area(self):
        with self.assertRaises(ValueError):
            await self.panel.action({"action": "join", "area": "AREA-A", "channel": "CHANNEL-B"})
        self.assertEqual([r.url.path for r in self.requests], ["/voice/channels"])
        self.requests.clear()
        result = await self.panel.action({"action": "join", "area": "AREA-A", "channel": "CHANNEL-A"})
        self.assertTrue(result["ok"])
        self.assertEqual(json.loads(self.requests[-1].content), {"area": "AREA-A", "channel": "CHANNEL-A"})

    async def test_join_disabled_never_sends_http(self):
        self.config["allow_join"] = False
        with self.assertRaises(ValueError):
            await self.panel.action({"action": "join", "area": "AREA-A", "channel": "CHANNEL-A"})
        self.assertEqual(self.requests, [])

    async def test_voice_operations_share_command_cooldown(self):
        await self.panel.action({"action": "leave"})
        self.assertIsNotNone(self.plugin._check_voice_cooldown())
        with self.assertRaises(ValueError):
            await self.panel.action({"action": "backend", "backend": "mimo"})
        self.assertEqual([r.url.path for r in self.requests], ["/voice/leave"])

    async def test_failed_voice_response_never_reports_success(self):
        self.responses["/voice/join"] = {"ok": False, "error": "join failed"}
        with self.assertRaises(Exception):
            await self.panel.action({"action": "join", "area": "AREA-A", "channel": "CHANNEL-A"})
        self.assertIsNone(self.plugin._check_voice_cooldown())

    async def test_backend_switch_reports_saved_but_restart_required(self):
        self.responses["/api/config"]["restart_required"] = True
        result = await self.panel.action({"action": "backend", "backend": "mimo"})
        self.assertFalse(result["applied"])
        self.assertIn("重启", result["message"])
        self.assertEqual(json.loads(self.requests[-1].content), {"updates": {"voice": {"backend": "mimo_cascade"}}})

    async def test_auto_visit_targets_area_and_keeps_pause_feedback(self):
        result = await self.panel.action({"action": "auto_visit", "area": "AREA-A", "enabled": True})
        self.assertIn("暂停", result["message"])
        self.assertEqual(json.loads(self.requests[-1].content), {"updates": {"areas": {"AREA-A": {"enabled": True}}}})

    async def test_unknown_action_or_non_boolean_visit_never_sends_http(self):
        for body in ({"action": "proxy", "url": "http://example.test"},
                     {"action": "auto_visit", "area": "AREA-A", "enabled": "false"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                await self.panel.action(body)
        self.assertEqual(self.requests, [])

    async def test_full_console_proxy_has_fixed_routes_and_server_side_auth(self):
        self.responses["/api/status"] = {"ok": True, "bridge": {"connected": True}}
        result = await self.panel.console({"method": "GET", "path": "/api/status", "params": {}})
        self.assertTrue(result["bridge"]["connected"])
        self.assertEqual(self.requests[-1].headers["Authorization"], "Bearer server-only-secret")
        for path in ("https://evil.example/", "/api/../admin", "/assets/config.py", "/api/%2e%2e/admin"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await self.panel.console({"method": "GET", "path": path})
        self.assertEqual(len(self.requests), 1)

    async def test_full_console_cannot_supply_token_or_override_destination(self):
        for body in ({"path": "/api/status", "params": {"token": "injected"}},
                     {"path": "/api/config", "method": "TRACE"},
                     {"path": "/api/config", "method": "POST", "body": []}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                await self.panel.console(body)
        self.assertEqual(self.requests, [])

    async def test_full_console_voice_operations_also_share_lock_and_cooldown(self):
        self.responses["/api/voice/leave"] = {"ok": True, "joined": False}
        await self.panel.console({"method": "POST", "path": "/api/voice/leave", "body": {}})
        with self.assertRaises(ValueError):
            await self.panel.action({"action": "backend", "backend": "mimo"})
        self.assertEqual([r.url.path for r in self.requests], ["/voice/leave"])

    async def test_full_console_persona_keeps_put_method(self):
        self.responses["/api/persona"] = {"ok": True, "persona": "Helpful voice bot"}
        result = await self.panel.console({"method": "PUT", "path": "/api/persona", "body": {"persona": "Helpful voice bot"}})
        self.assertEqual(result["persona"], "Helpful voice bot")
        self.assertEqual(self.requests[0].method, "PUT")

    async def test_full_console_save_and_reconnect_are_one_workflow(self):
        self.responses["/api/bridge/restart"] = {"ok": True}
        await self.panel.console({"method": "POST", "path": "/api/config", "body": {"updates": {"voice": {"backend": "mimo_cascade"}}}})
        await self.panel.console({"method": "POST", "path": "/api/bridge/restart", "body": {}})
        self.assertEqual([r.url.path for r in self.requests], ["/api/config", "/api/bridge/restart"])
        with self.assertRaises(ValueError):
            await self.panel.action({"action": "leave"})

    async def test_non_voice_console_writes_do_not_start_voice_cooldown(self):
        self.responses["/api/persona"] = {"ok": True}
        await self.panel.console({"method": "PUT", "path": "/api/persona", "body": {"persona": "Hello"}})
        await self.panel.action({"action": "leave"})

    async def test_malformed_action_is_validation_error(self):
        for action in ([], {}):
            with self.subTest(action=action), self.assertRaises(ValueError):
                await self.panel.action({"action": action})

    async def test_queued_voice_write_cannot_run_after_termination(self):
        await self.plugin._voice_op_lock.acquire()
        operation = asyncio.create_task(self.panel.action({"action": "leave"}))
        await asyncio.sleep(0)
        terminating = asyncio.create_task(self.plugin.terminate())
        await asyncio.sleep(0)
        self.plugin._voice_op_lock.release()
        with self.assertRaises(ValueError):
            await operation
        await terminating
        self.assertEqual(self.requests, [])

    async def test_full_console_default_join_resolves_domain_before_joining(self):
        await self.panel.console({"method": "POST", "path": "/api/voice/join", "body": {"area": "", "channel": ""}})
        self.assertEqual(json.loads(self.requests[-1].content), {"area": "AREA-A", "channel": "CHANNEL-A"})

    async def test_preview_and_diagnostics_do_not_block_real_voice_controls(self):
        for path in ("/api/voice/preview", "/api/voice/diagnostics"):
            self.responses[path] = {"ok": True}
            await self.panel.console({"method": "POST", "path": path, "body": {"text": "你好"}})
        await self.panel.action({"action": "leave"})

    async def test_new_console_routes_preserve_methods_and_query(self):
        for method, path, params in (("GET", "/api/voice/preview/prompts", {"kind": "enter", "area": "AREA-A"}),
                                     ("GET", "/api/maintenance", {}),
                                     ("POST", "/api/maintenance/check", {}),
                                     ("POST", "/api/maintenance/backups", {}),
                                     ("POST", "/api/maintenance/update", {}),
                                     ("POST", "/api/maintenance/restore", {})):
            self.responses[path] = {"ok": True}
            await self.panel.console({"method": method, "path": path, "params": params, "body": {"channel": "dev"}})
            self.assertEqual(self.requests[-1].method, method)
            self.assertEqual(dict(self.requests[-1].url.params), params)

    async def test_full_console_does_not_apply_default_channel_to_other_area(self):
        with self.assertRaises(ValueError):
            await self.panel.console({"method": "POST", "path": "/api/voice/join", "body": {"area": "AREA-B", "channel": ""}})
        self.assertEqual([r.url.path for r in self.requests], ["/voice/status"])


if __name__ == "__main__":
    unittest.main()

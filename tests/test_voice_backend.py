"""语音方案切换：HTTP 契约、指令/工具权限与运行状态反馈。"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

import ooptra_client
from test_main_logic import FakeClient, FakeEvent, _plugin, _run_first
from tools import mock_voice_api


class TestBackendNames(unittest.TestCase):
    def test_supported_aliases(self):
        for name in ("gemini", "Gemini Live", "gemini_live", "gemini-live"):
            with self.subTest(name=name):
                self.assertEqual(ooptra_client.normalize_backend(name), "gemini_live")
        for name in ("mimo", "MiMo 级联", "mimo_cascade", "mimo-cascade"):
            with self.subTest(name=name):
                self.assertEqual(ooptra_client.normalize_backend(name), "mimo_cascade")

    def test_unknown_names_are_rejected(self):
        for name in ("", "auto", "openai_realtime", "gemini mimO"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                ooptra_client.normalize_backend(name)


class TestBackendHTTP(unittest.TestCase):
    def test_switch_updates_only_backend_and_uses_bearer_token(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={
                "ok": True, "changed": {"voice": ["backend"]},
                "hot_reloaded_fields": ["backend"], "restart_required": False,
                "notes": ["语音后端已切换"],
            })

        client = ooptra_client.OoptraClient(
            "http://127.0.0.1:3090", token="test-token",
            transport=httpx.MockTransport(handler),
        )
        result = asyncio.run(client.set_backend("MiMo 级联"))
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url.path, "/api/config")
        self.assertEqual(request.headers["Authorization"], "Bearer test-token")
        self.assertEqual(json.loads(request.content), {
            "updates": {"voice": {"backend": "mimo_cascade"}},
        })
        self.assertEqual(result["hot_reloaded_fields"], ["backend"])

    def test_invalid_backend_does_not_send_a_request(self):
        requests = []
        client = ooptra_client.OoptraClient(
            "http://127.0.0.1:3090",
            transport=httpx.MockTransport(lambda r: requests.append(r)),
        )
        with self.assertRaises(ValueError):
            asyncio.run(client.set_backend("invalid"))
        self.assertEqual(requests, [])

    def test_missing_config_route_explains_webui_requirement(self):
        client = ooptra_client.OoptraClient(
            "http://127.0.0.1:3091",
            transport=httpx.MockTransport(lambda r: httpx.Response(404, json={"ok": False})),
        )
        with self.assertRaises(ooptra_client.OoptraError) as error:
            asyncio.run(client.set_backend("gemini"))
        self.assertEqual(error.exception.status_code, 404)
        self.assertIn("WebUI", str(error.exception))
        self.assertIn("/api/config", str(error.exception))

    def test_non_acknowledgements_are_not_reported_as_saved(self):
        for body in ([], {}, {"ok": True}, {"ok": True, "changed": {"voice": []}}):
            with self.subTest(body=body):
                client = ooptra_client.OoptraClient(
                    "http://127.0.0.1:3090",
                    transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
                )
                with self.assertRaises(ooptra_client.OoptraError):
                    asyncio.run(client.set_backend("gemini"))

    def test_auth_failure_does_not_retry_or_use_another_endpoint(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(401, json={"ok": False, "error": "unauthorized"})

        client = ooptra_client.OoptraClient(
            "http://127.0.0.1:3090", transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(ooptra_client.OoptraError) as error:
            asyncio.run(client.set_backend("mimo"))
        self.assertEqual(error.exception.status_code, 401)
        self.assertEqual(len(requests), 1)


class TestMockBackendAPI(unittest.TestCase):
    @staticmethod
    def dispatch(request):
        handler = mock_voice_api.Handler.__new__(mock_voice_api.Handler)
        handler.path = str(request.url.raw_path, "ascii")
        handler.headers = {"Content-Length": str(len(request.content))}
        handler.rfile = io.BytesIO(request.content)
        responses = []
        handler._json = lambda code, body: responses.append(httpx.Response(code, json=body))
        if request.method == "POST":
            handler.do_POST()
        else:
            handler.do_GET()
        return responses[0]

    def test_command_switches_both_ways_and_query_reads_mock_state(self):
        async def scenario():
            plugin, _ = _plugin()
            plugin._client = ooptra_client.OoptraClient(
                "http://127.0.0.1:3090", transport=httpx.MockTransport(self.dispatch),
            )
            admin = FakeEvent(admin=True)
            original_channel = mock_voice_api.JOINED["channel"]
            for name, expected in (("mimo", "MiMo 级联"), ("gemini", "Gemini Live")):
                plugin._last_voice_op_at = 0
                result = await plugin.voice_backend(admin, name).__anext__()
                self.assertIn("已切换", result)
                self.assertIn(expected, result)
                queried = await plugin.tool_backend(FakeEvent())
                self.assertIn(expected, queried)
                self.assertEqual(mock_voice_api.JOINED["channel"], original_channel)

        with patch.dict(mock_voice_api.JOINED, mock_voice_api.JOINED.copy(), clear=True):
            asyncio.run(scenario())

    def test_mock_rejects_invalid_backend_and_malformed_payloads(self):
        for payload in ([], {}, {"updates": []}, {"updates": {"voice": {"backend": "auto"}}}):
            with self.subTest(payload=payload):
                response = self.dispatch(httpx.Request("POST", "http://local/api/config", json=payload))
                self.assertEqual(response.status_code, 400)

    def test_schema_defaults_to_admin_only(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text("utf-8"))
        self.assertIs(schema["backend_admin_only"]["default"], True)


class TestBackendCommands(unittest.TestCase):
    def attach(self, plugin, **status):
        client = FakeClient(status={"backend": "gemini_live", "enabled": True, **status})
        client.set_backend = AsyncMock(return_value={
            "ok": True, "changed": {"voice": ["backend"]},
            "hot_reloaded_fields": ["backend"], "restart_required": False,
            "notes": ["语音后端已切换"],
        })
        plugin._client = client
        return client

    def test_everyone_can_query_without_group_binding(self):
        plugin, _ = _plugin()
        client = self.attach(plugin)
        text = _run_first(plugin.voice_backend(FakeEvent(group_id="")))
        self.assertIn("Gemini Live", text)
        client.set_backend.assert_not_awaited()

    def test_switch_is_admin_only_even_when_old_config_lacks_new_key(self):
        plugin, _ = _plugin()
        client = self.attach(plugin)
        text = _run_first(plugin.voice_backend(FakeEvent(), "mimo"))
        self.assertIn("仅管理员", text)
        client.set_backend.assert_not_awaited()

    def test_admin_can_switch_using_two_word_name(self):
        plugin, _ = _plugin()
        client = self.attach(plugin)
        text = _run_first(plugin.voice_backend(FakeEvent(admin=True), "MiMo", "级联"))
        self.assertIn("MiMo 级联", text)
        self.assertIn("已切换", text)
        self.assertIn("全局", text)
        client.set_backend.assert_awaited_once_with("mimo_cascade")

    def test_explicit_policy_override_allows_members(self):
        plugin, _ = _plugin(backend_admin_only=False)
        client = self.attach(plugin)
        text = _run_first(plugin.voice_backend(FakeEvent(), "gemini"))
        self.assertIn("已切换", text)
        client.set_backend.assert_awaited_once_with("gemini_live")

    def test_invalid_name_returns_usage_without_mutating(self):
        plugin, _ = _plugin()
        client = self.attach(plugin)
        text = _run_first(plugin.voice_backend(FakeEvent(admin=True), "auto"))
        self.assertIn("/语音方案 gemini", text)
        client.set_backend.assert_not_awaited()

    def test_disabled_agent_is_shown_on_query(self):
        plugin, _ = _plugin()
        self.attach(plugin, enabled=False)
        text = _run_first(plugin.voice_backend(FakeEvent()))
        self.assertIn("未启用", text)

    def test_missing_backend_is_not_reported_as_gemini(self):
        plugin, _ = _plugin()
        self.attach(plugin, backend="")
        text = _run_first(plugin.voice_backend(FakeEvent()))
        self.assertIn("未返回", text)
        self.assertNotIn("当前语音方案：Gemini", text)

    def test_api_failure_does_not_report_success(self):
        plugin, _ = _plugin()
        client = self.attach(plugin)
        client.set_backend.side_effect = ooptra_client.OoptraError("鉴权失败")
        text = _run_first(plugin.voice_backend(FakeEvent(admin=True), "mimo"))
        self.assertIn("鉴权失败", text)
        self.assertNotIn("已切换", text)
        self.assertEqual(plugin._last_voice_op_at, 0)

    def test_session_rebuild_failure_is_not_reported_as_success(self):
        plugin, _ = _plugin()
        client = self.attach(plugin)
        client.set_backend.return_value["notes"] = ["语音会话重建失败：missing API key"]
        text = _run_first(plugin.voice_backend(FakeEvent(admin=True), "gemini"))
        self.assertIn("配置已保存", text)
        self.assertIn("语音会话重建失败", text)
        self.assertNotIn("已切换", text)

    def test_old_server_reports_saved_and_restart_needed(self):
        plugin, _ = _plugin()
        client = self.attach(plugin)
        client.set_backend.return_value.pop("hot_reloaded_fields")
        client.set_backend.return_value["notes"] = []
        client.set_backend.return_value["restart_required"] = True
        text = _run_first(plugin.voice_backend(FakeEvent(admin=True), "mimo"))
        self.assertIn("配置已保存", text)
        self.assertIn("重启", text)
        self.assertNotIn("已切换", text)

    def test_long_notes_do_not_hide_runtime_failure(self):
        plugin, _ = _plugin()
        client = self.attach(plugin)
        client.set_backend.return_value["notes"] = ["提示"] * 8 + ["语音配置应用失败：session error"]
        text = _run_first(plugin.voice_backend(FakeEvent(admin=True), "gemini"))
        self.assertIn("运行时应用出现异常", text)
        self.assertNotIn("已切换", text)

    def test_command_and_tool_concurrent_switches_share_cooldown(self):
        async def scenario():
            plugin, _ = _plugin()
            client = self.attach(plugin)
            admin = FakeEvent(admin=True)
            async with plugin._voice_op_lock:
                command = asyncio.create_task(plugin.voice_backend(admin, "mimo").__anext__())
                tool = asyncio.create_task(plugin.tool_backend(admin, "gemini"))
                await asyncio.sleep(0)
            results = await asyncio.gather(command, tool)
            self.assertEqual(client.set_backend.await_count, 1)
            self.assertTrue(any("频繁" in text for text in results))
            self.assertFalse(plugin._voice_op_lock.locked())

        asyncio.run(scenario())

    def test_llm_tool_cannot_bypass_admin_permission(self):
        plugin, _ = _plugin()
        client = self.attach(plugin)
        text = asyncio.run(plugin.tool_backend(FakeEvent(), "mimo"))
        self.assertIn("仅管理员", text)
        client.set_backend.assert_not_awaited()

    def test_llm_tool_respects_global_tool_switch(self):
        plugin, _ = _plugin(enable_llm_tools=False)
        client = self.attach(plugin)
        text = asyncio.run(plugin.tool_backend(FakeEvent(admin=True), "mimo"))
        self.assertIn("已禁用", text)
        client.set_backend.assert_not_awaited()

    def test_llm_tool_can_query_or_switch_as_admin(self):
        plugin, _ = _plugin()
        client = self.attach(plugin)
        self.assertIn("Gemini Live", asyncio.run(plugin.tool_backend(FakeEvent())))
        text = asyncio.run(plugin.tool_backend(FakeEvent(admin=True), "mimo"))
        self.assertIn("已切换", text)
        client.set_backend.assert_awaited_once_with("mimo_cascade")

    def test_help_exposes_command_and_permission_default(self):
        plugin, _ = _plugin()
        text = _run_first(plugin.voice_help(FakeEvent()))
        self.assertIn("/语音方案", text)
        self.assertIn("方案切换：仅管理员", text)


if __name__ == "__main__":
    unittest.main()

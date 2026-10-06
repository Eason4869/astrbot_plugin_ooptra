"""管理员串门命令：分域开关、真实 HTTP 契约与失败反馈。"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from test_main_logic import FakeClient, FakeEvent, _plugin, _run_first

from ooptra_client import OoptraClient, OoptraError
from tools import mock_voice_api


def acknowledgement(area="AREA-A", enabled=True):
    return {
        "ok": True,
        "changed": {"auto_visit": ["areas"]},
        "config": {"areas": {area: {"enabled": enabled}}},
        "status": {"areas": {area: {"enabled": enabled}}, "paused": False},
        "notes": [],
    }


class TestAutoVisitHTTP(unittest.TestCase):
    def test_sparse_patch_uses_bearer_and_confirms_both_states(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                requests = []

                def handler(request, requests=requests, enabled=enabled):
                    requests.append(request)
                    return httpx.Response(200, json=acknowledgement(enabled=enabled))

                client = OoptraClient(
                    "http://127.0.0.1:3090", token="test-token",
                    transport=httpx.MockTransport(handler),
                )
                result = asyncio.run(client.set_auto_visit("AREA-A", enabled))
                self.assertEqual(len(requests), 1)
                request = requests[0]
                self.assertEqual(request.method, "POST")
                self.assertEqual(request.url.path, "/voice/auto-visit/config")
                self.assertEqual(request.headers["Authorization"], "Bearer test-token")
                self.assertEqual(json.loads(request.content), {
                    "updates": {"areas": {"AREA-A": {"enabled": enabled}}},
                })
                self.assertIs(result["status"]["areas"]["AREA-A"]["enabled"], enabled)

    def test_invalid_inputs_never_send_http(self):
        requests = []
        client = OoptraClient("http://localhost", transport=httpx.MockTransport(requests.append))
        for area, enabled in (("", True), ("AREA-A", "开"), ("AREA-A", 1)):
            with self.subTest(area=area, enabled=enabled), self.assertRaises(ValueError):
                asyncio.run(client.set_auto_visit(area, enabled))
        self.assertEqual(requests, [])

    def test_missing_endpoint_explains_minimum_version(self):
        client = OoptraClient("http://localhost", transport=httpx.MockTransport(
            lambda r: httpx.Response(404, json={"ok": False}),
        ))
        with self.assertRaises(OoptraError) as error:
            asyncio.run(client.set_auto_visit("AREA-A", True))
        self.assertEqual(error.exception.status_code, 404)
        self.assertIn("3.0", str(error.exception))
        self.assertIn("/voice/auto-visit/config", str(error.exception))

    def test_unconfirmed_or_mismatched_responses_fail(self):
        bodies = [[], {}, {"ok": True}, acknowledgement(enabled=False)]
        for key in ("changed", "config", "status"):
            body = acknowledgement()
            body.pop(key)
            bodies.append(body)
        body = acknowledgement()
        body["status"]["areas"]["AREA-A"]["enabled"] = False
        bodies.append(body)
        body = acknowledgement()
        body["config"]["areas"]["AREA-A"]["enabled"] = 1
        bodies.append(body)
        for body in bodies:
            with self.subTest(body=body):
                client = OoptraClient("http://localhost", transport=httpx.MockTransport(
                    lambda r, body=body: httpx.Response(200, json=body),
                ))
                with self.assertRaises(OoptraError):
                    asyncio.run(client.set_auto_visit("AREA-A", True))

    def test_auth_and_reload_failures_are_not_retried(self):
        for status, message in ((401, "unauthorized"), (503, "配置已保存，但应用失败，自动加入已暂停")):
            with self.subTest(status=status):
                requests = []

                def handler(request, requests=requests, status=status, message=message):
                    requests.append(request)
                    return httpx.Response(status, json={"ok": False, "error": message})

                client = OoptraClient("http://localhost", transport=httpx.MockTransport(handler))
                with self.assertRaises(OoptraError) as error:
                    asyncio.run(client.set_auto_visit("AREA-A", True))
                self.assertEqual(error.exception.status_code, status)
                self.assertEqual(len(requests), 1)
                if status == 503:
                    self.assertIn(message, str(error.exception))


class TestAutoVisitCommand(unittest.TestCase):
    def attach(self, plugin, **status):
        client = FakeClient(status={"default_area": "AREA-A", **status})
        client.status = AsyncMock(side_effect=client.status)
        client.set_auto_visit = AsyncMock(return_value=acknowledgement())
        plugin._client = client
        return client

    def test_non_admin_cannot_control_even_with_other_permissions_disabled(self):
        plugin, _ = _plugin(join_admin_only=False, backend_admin_only=False)
        client = self.attach(plugin)
        text = _run_first(plugin.voice_auto_visit(FakeEvent(), "开"))
        self.assertIn("仅管理员", text)
        client.status.assert_not_awaited()
        client.set_auto_visit.assert_not_awaited()

    def test_failed_admin_check_denies_access(self):
        plugin, _ = _plugin()
        client = self.attach(plugin)
        event = FakeEvent(admin=True)
        event.is_admin = lambda: (_ for _ in ()).throw(RuntimeError("no role"))
        self.assertIn("仅管理员", _run_first(plugin.voice_auto_visit(event, "开")))
        client.status.assert_not_awaited()

    def test_missing_or_invalid_argument_shows_usage_without_http(self):
        for argument in ("", "on", "开启", "开 关"):
            with self.subTest(argument=argument):
                plugin, _ = _plugin()
                client = self.attach(plugin)
                text = _run_first(plugin.voice_auto_visit(FakeEvent(admin=True), argument))
                self.assertIn("/语音串门 开|关", text)
                client.status.assert_not_awaited()
                client.set_auto_visit.assert_not_awaited()

    def test_admin_switches_only_bound_area_and_releases_lock_before_yield(self):
        for argument, enabled in ((" 开 ", True), ("关", False)):
            with self.subTest(argument=argument):
                plugin, _ = _plugin(group_map={"123456": {"area": "AREA-A"}})
                client = self.attach(plugin)
                client.set_auto_visit.return_value = acknowledgement(enabled=enabled)
                text = _run_first(plugin.voice_auto_visit(FakeEvent(admin=True), argument))
                self.assertIn("已开启" if enabled else "已关闭", text)
                self.assertIn("AREA-A", text)
                client.set_auto_visit.assert_awaited_once_with("AREA-A", enabled)
                self.assertFalse(plugin._voice_op_lock.locked())

    def test_multiple_areas_use_ooptra_default_area(self):
        plugin, _ = _plugin(group_map={"123456": {"areas": ["AREA-B", "AREA-A"]}})
        client = self.attach(plugin)
        _run_first(plugin.voice_auto_visit(FakeEvent(admin=True), "开"))
        client.set_auto_visit.assert_awaited_once_with("AREA-A", True)

    def test_unbound_default_uses_first_bound_area(self):
        plugin, _ = _plugin(group_map={"123456": {"areas": ["AREA-A", "AREA-B"]}})
        client = self.attach(plugin, default_area="OTHER-AREA")
        _run_first(plugin.voice_auto_visit(FakeEvent(admin=True), "开"))
        client.set_auto_visit.assert_awaited_once_with("AREA-A", True)

    def test_shares_cooldown_with_other_voice_operations(self):
        plugin, _ = _plugin(group_map={"123456": {"area": "AREA-A"}})
        client = self.attach(plugin)
        plugin._mark_voice_op()
        self.assertIn("频繁", _run_first(plugin.voice_auto_visit(FakeEvent(admin=True), "开")))
        client.set_auto_visit.assert_not_awaited()

    def test_unbound_group_and_private_chat_do_not_use_global_default(self):
        for group_id in ("123456", ""):
            with self.subTest(group_id=group_id):
                plugin, _ = _plugin()
                client = self.attach(plugin)
                text = _run_first(plugin.voice_auto_visit(FakeEvent(group_id, admin=True), "开"))
                self.assertIn("未绑定", text)
                client.set_auto_visit.assert_not_awaited()

    def test_errors_do_not_claim_success_or_start_cooldown(self):
        plugin, _ = _plugin(group_map={"123456": {"area": "AREA-A"}})
        client = self.attach(plugin)
        client.set_auto_visit.side_effect = OoptraError("配置已保存，但应用失败", status_code=503)
        text = _run_first(plugin.voice_auto_visit(FakeEvent(admin=True), "开"))
        self.assertIn("应用失败", text)
        self.assertNotIn("已开启", text)
        self.assertEqual(plugin._last_voice_op_at, 0)
        self.assertFalse(plugin._voice_op_lock.locked())

    def test_paused_controller_is_not_automatically_resumed(self):
        plugin, _ = _plugin(group_map={"123456": {"area": "AREA-A"}})
        client = self.attach(plugin)
        client.set_auto_visit.return_value["status"].update(paused=True, pause_reason="状态记录异常")
        text = _run_first(plugin.voice_auto_visit(FakeEvent(admin=True), "开"))
        self.assertIn("已开启", text)
        self.assertIn("暂停", text)
        self.assertIn("状态记录异常", text)

    def test_parallel_commands_share_cooldown_and_release_lock(self):
        plugin, _ = _plugin(group_map={"123456": {"area": "AREA-A"}})
        client = self.attach(plugin)

        async def scenario():
            async def run(argument):
                return await anext(plugin.voice_auto_visit(FakeEvent(admin=True), argument))

            texts = await asyncio.gather(run("开"), run("关"))
            self.assertEqual(client.set_auto_visit.await_count, 1)
            self.assertTrue(any("频繁" in text for text in texts))
            self.assertFalse(plugin._voice_op_lock.locked())

        asyncio.run(scenario())

    def test_help_lists_admin_only_command(self):
        plugin, _ = _plugin()
        text = _run_first(plugin.voice_help(FakeEvent()))
        self.assertIn("/语音串门 开|关", text)
        self.assertIn("（管理）", text)


class TestAutoVisitMock(unittest.TestCase):
    def test_mock_route_preserves_other_area_and_overrides(self):
        original = deepcopy(mock_voice_api.AUTO_VISIT)
        try:
            mock_voice_api.AUTO_VISIT.update(areas={
                "demo-area": {"enabled": False, "daily_limit": 2, "overrides": {"join_probability": 0.5}},
                "demo-area-2": {"enabled": True},
            })
            for enabled in (True, False):
                raw = json.dumps({"updates": {"areas": {"demo-area": {"enabled": enabled}}}}).encode()
                handler = mock_voice_api.Handler.__new__(mock_voice_api.Handler)
                handler.path = "/voice/auto-visit/config"
                handler.headers = {"Content-Length": str(len(raw))}
                handler.rfile = BytesIO(raw)
                responses = []
                handler._json = lambda status, payload, responses=responses: responses.append((status, payload))
                handler.do_POST()
                self.assertEqual(responses[0][0], 200)
                payload = responses[0][1]
                self.assertIs(payload["status"]["areas"]["demo-area"]["enabled"], enabled)
                self.assertEqual(payload["config"]["areas"]["demo-area"]["daily_limit"], 2)
                self.assertEqual(payload["config"]["areas"]["demo-area"]["overrides"], {"join_probability": 0.5})
                self.assertIs(payload["config"]["areas"]["demo-area-2"]["enabled"], True)
        finally:
            mock_voice_api.AUTO_VISIT.clear()
            mock_voice_api.AUTO_VISIT.update(original)


if __name__ == "__main__":
    unittest.main()

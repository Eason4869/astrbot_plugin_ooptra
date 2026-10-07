"""Embedded console authenticates using the configured server credential, not cookies."""
import json
import unittest

import httpx
from test_main_logic import FakeConfig, _main

from ooptra_client import OoptraClient, OoptraError


class TestConsoleAuth(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = FakeConfig(api_base="http://deployed:3090", api_token="correct-password", group_map={})
        self.plugin = _main().OoptraPlugin(None, self.config)
        self.password = "correct-password"
        self.calls = []

        def upstream(request):
            self.calls.append(request)
            if request.url.path == "/api/auth/status":
                return httpx.Response(200, json={"ok": True, "configured": bool(self.password), "authenticated": False, "setup_allowed": not self.password})
            if request.url.path in {"/api/auth/login", "/api/auth/setup"}:
                entered = json.loads(request.content)["password"]
                if request.url.path.endswith("setup") and not self.password:
                    self.password = entered
                if entered == self.password:
                    return httpx.Response(200, json={"ok": True}, headers={"Set-Cookie": "ooptra_session=test-session; HttpOnly"})
                return httpx.Response(401, json={"ok": False, "error": "控制台密码不正确"})
            if request.url.path == "/api/auth/logout":
                return httpx.Response(200, json={"ok": True})
            if request.headers.get("Authorization") == "Bearer " + self.password:
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(401, json={"ok": False, "error": "需要登录"})

        self.transport = httpx.MockTransport(upstream)
        self.plugin._client = OoptraClient(self.config["api_base"], self.config["api_token"], transport=self.transport)

    async def test_startup_confirms_bearer_even_when_native_cookie_status_is_false(self):
        result = await self.plugin.panel.console({"path": "/api/auth/status"})
        self.assertTrue(result["authenticated"])
        self.assertEqual([r.url.path for r in self.calls], ["/api/auth/status", "/api/status"])
        self.assertNotIn("correct-password", str(result))

    async def test_wrong_configured_token_prompts_login_without_claiming_authentication(self):
        self.config["api_token"] = "outdated-password"
        self.plugin._client = OoptraClient(self.config["api_base"], "outdated-password", transport=self.transport)
        result = await self.plugin.panel.console({"path": "/api/auth/status"})
        self.assertFalse(result["authenticated"])

    async def test_login_updates_plugin_token_only_after_upstream_confirmation(self):
        self.config["api_token"] = "outdated-password"
        self.plugin._client = OoptraClient(self.config["api_base"], "outdated-password", transport=self.transport)
        result = await self.plugin.panel.console({"path": "/api/auth/login", "method": "POST", "body": {"password": "correct-password"}})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.config["api_token"], "correct-password")
        self.assertEqual(self.config.saved, 1)
        self.assertEqual(self.plugin.client.token, "correct-password")

    async def test_incorrect_login_never_changes_saved_configuration(self):
        with self.assertRaises(OoptraError):
            await self.plugin.panel.console({"path": "/api/auth/login", "method": "POST", "body": {"password": "wrong-password"}})
        self.assertEqual(self.config["api_token"], "correct-password")
        self.assertEqual(self.config.saved, 0)

    async def test_failed_persistence_restores_the_previous_plugin_token(self):
        self.config["api_token"] = "outdated-password"
        self.plugin._client = OoptraClient(self.config["api_base"], "outdated-password", transport=self.transport)
        def fail():
            raise OSError("disk full")
        self.config.save_config = fail
        with self.assertRaises(OoptraError):
            await self.plugin.panel.console({"path": "/api/auth/login", "method": "POST", "body": {"password": "correct-password"}})
        self.assertEqual(self.config["api_token"], "outdated-password")

    async def test_runtime_persistence_failure_also_restores_old_token(self):
        self.config["api_token"] = "outdated-password"
        self.plugin._client = OoptraClient(self.config["api_base"], "outdated-password", transport=self.transport)
        def fail():
            raise RuntimeError("save failed")
        self.config.save_config = fail
        with self.assertRaises(OoptraError):
            await self.plugin.panel.console({"path": "/api/auth/login", "method": "POST", "body": {"password": "correct-password"}})
        self.assertEqual(self.config["api_token"], "outdated-password")
        self.assertEqual(self.plugin.client.token, "outdated-password")

    async def test_unicode_password_works_as_a_bearer_after_confirmed_login(self):
        self.password = "中文密码🔒"
        await self.plugin.panel.console({"path": "/api/auth/login", "method": "POST", "body": {"password": self.password}})
        self.assertTrue((await self.plugin.panel.console({"path": "/api/auth/status"}))["authenticated"])

    async def test_control_characters_are_rejected_before_setup_can_change_server(self):
        self.password = ""
        for password in ("abc\r\ndef", "abc\x00def", "abc\x7fdef"):
            with self.subTest(password=repr(password)), self.assertRaises(ValueError):
                await self.plugin.panel.console({"path": "/api/auth/setup", "method": "POST", "body": {"password": password}})
        self.assertEqual(self.password, "")
        self.assertEqual(self.calls, [])

    async def test_setup_persistence_failure_explains_reopening_with_the_same_password(self):
        self.password = ""
        def fail():
            raise OSError("disk full")
        self.config.save_config = fail
        with self.assertRaisesRegex(OoptraError, "重新打开.*同一密码"):
            await self.plugin.panel.console({"path": "/api/auth/setup", "method": "POST", "body": {"password": "first-password"}})
        self.assertEqual(self.password, "first-password")
        self.assertEqual(self.config["api_token"], "correct-password")

    async def test_setup_is_forwarded_to_upstream_local_policy_and_then_persisted(self):
        self.password = ""
        result = await self.plugin.panel.console({"path": "/api/auth/setup", "method": "POST", "body": {"password": "first-password"}})
        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.config["api_token"], "first-password")

    async def test_network_failure_does_not_masquerade_as_bad_credentials(self):
        async def upstream(request):
            if request.url.path == "/api/auth/status":
                return httpx.Response(200, json={"ok": True, "configured": True, "authenticated": False})
            raise httpx.ConnectError("offline", request=request)
        self.plugin._client._transport = httpx.MockTransport(upstream)
        with self.assertRaises(OoptraError):
            await self.plugin.panel.console({"path": "/api/auth/status"})

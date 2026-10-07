"""The deployed HTML and every script, rather than a bundled snapshot, are authoritative."""
import base64
import unittest

import httpx
from test_main_logic import FakeConfig, _main

from ooptra_client import OoptraClient, OoptraError


class TestLiveConsole(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plugin = _main().OoptraPlugin(None, FakeConfig(api_base="http://deployed:3090", api_token="backend-secret", group_map={}))
        self.requests = []
        self.version = "old"
        self.resources = {
            "/assets/style.css": ("text/css", b"body{color:blue}"),
            "/assets/logo.svg": ("image/svg+xml", b"<svg/>"),
            "/assets/app.js": ("text/javascript", b"async function api(path, opts = {}) { return fetch(path); }\nfunction toast() {}\nlet v=localStorage.getItem('x');"),
            "/assets/config.js": ("text/javascript", b"window.CONFIG_LOADED=true;"),
            "/assets/voice.js": ("text/javascript", b"window.VOICE_LOADED=true;"),
        }

        def upstream(request):
            self.requests.append(request)
            if request.url.path == "/":
                return httpx.Response(200, text=f'<!doctype html><html><head><link rel="stylesheet" href="/assets/style.css?v=1"></head><body><h1>{self.version}</h1><img src="/assets/logo.svg"><script src="/assets/app.js"></script><script src="/assets/config.js"></script><script src="/assets/voice.js"></script></body></html>', headers={"Content-Type": "text/html"})
            mime, content = self.resources[request.url.path]
            return httpx.Response(200, content=content, headers={"Content-Type": mime})

        self.plugin._client = OoptraClient("http://deployed:3090", "backend-secret", transport=httpx.MockTransport(upstream))

    async def test_reload_reads_new_deployed_html_and_split_scripts_with_backend_auth(self):
        first = await self.plugin.panel.console_page()
        self.version = "new deployment"
        second = await self.plugin.panel.console_page()
        self.assertIn("old", first["html"])
        self.assertIn("new deployment", second["html"])
        self.assertIn("CONFIG_LOADED", second["scripts"][1])
        self.assertIn("VOICE_LOADED", second["scripts"][2])
        self.assertIn("OoptraPanel.storage", second["scripts"][0])
        self.assertIn("OoptraPanel.api", second["scripts"][0])
        self.assertNotIn("<script", second["html"])
        self.assertIn(base64.b64encode(b"<svg/>").decode(), second["html"])
        self.assertTrue(all(r.url.host == "deployed" for r in self.requests))
        self.assertTrue(all(r.headers["Authorization"] == "Bearer backend-secret" for r in self.requests))
        self.assertNotIn("backend-secret", str(second))

    async def test_external_and_traversal_resources_fail_without_fetching_them(self):
        for resource in ("http://evil.invalid/app.js", "//evil.invalid/app.js", "/assets/../config.py", "/assets/%2e%2e/config.py"):
            with self.subTest(resource=resource):
                self.requests.clear()
                self.plugin._client._transport = httpx.MockTransport(lambda r: httpx.Response(200, text=f'<html><script src="{resource}"></script></html>', headers={"Content-Type": "text/html"}))
                with self.assertRaises(OoptraError):
                    await self.plugin.panel.console_page()

    async def test_inactive_console_page_never_contacts_upstream(self):
        self.plugin._webui_active = False
        with self.assertRaises(ValueError):
            await self.plugin.panel.console_page()
        self.assertEqual(self.requests, [])

    async def test_missing_resource_and_auth_failure_report_safe_errors(self):
        for status in (401, 404, 503):
            self.plugin._client._transport = httpx.MockTransport(lambda r: httpx.Response(status, text="backend-secret"))
            with self.assertRaises(OoptraError) as error:
                await self.plugin.panel.console_page()
            self.assertNotIn("backend-secret", str(error.exception))

    async def test_resource_size_limit_is_enforced_while_streaming(self):
        self.plugin._client._transport = httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1), headers={"Content-Type": "text/html"}))
        with self.assertRaises(OoptraError):
            await self.plugin.panel.console_page()

    async def test_external_css_dependencies_report_incompatibility(self):
        self.resources["/assets/style.css"] = ("text/css", b"body{background:url(/assets/bg.png)}")
        with self.assertRaises(OoptraError):
            await self.plugin.panel.console_page()

    async def test_changed_api_adapter_shape_reports_incompatibility(self):
        self.resources["/assets/app.js"] = ("text/javascript", b"const api = async () => ({});")
        with self.assertRaises(OoptraError):
            await self.plugin.panel.console_page()

    async def test_console_api_on_configured_instance_and_unsafe_or_unknown_paths_rejected(self):
        self.plugin._client._transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True, "version": "new"}))
        result = await self.plugin.panel.console({"path": "/api/maintenance", "params": {"page": "2", "page_size": "10"}})
        self.assertEqual(result["version"], "new")
        for path in ("http://evil/api/status", "//evil/api/status", "/api/../config.py", "/api/%2e%2e/config.py", "/api/status?token=x", "/assets/app.js", "/api/unknown"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await self.plugin.panel.console({"path": path})

    async def test_deployed_maintenance_sections_and_specific_operations(self):
        self.plugin._client._transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
        for method, path in (("GET", "/api/maintenance/preflight"), ("GET", "/api/maintenance/storage"),
                             ("POST", "/api/maintenance/network/test"), ("POST", "/api/maintenance/cleanup/preview"),
                             ("POST", "/api/maintenance/cleanup/apply"), ("DELETE", "/api/maintenance/backups/" + "a" * 32)):
            with self.subTest(path=path):
                self.assertTrue((await self.plugin.panel.console({"method": method, "path": path}))["ok"])
        for path in ("/api/maintenance/backups/wrong-id", "/api/maintenance/backups/" + "a" * 33):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await self.plugin.panel.console({"method": "DELETE", "path": path})

    async def test_maintenance_sections_allow_the_deployed_twelve_second_query(self):
        recorded = []
        def upstream(request):
            recorded.append(request.extensions["timeout"]["read"])
            return httpx.Response(200, json={"ok": True})
        self.plugin._client._transport = httpx.MockTransport(upstream)
        for path in ("/api/maintenance/preflight", "/api/maintenance/storage"):
            await self.plugin.panel.console({"path": path})
        self.assertTrue(all(timeout >= 20 for timeout in recorded))

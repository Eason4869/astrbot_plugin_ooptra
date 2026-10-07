"""Optional real-Quart integration checks for authenticated plugin handlers."""

from __future__ import annotations

import asyncio
import unittest

from test_main_logic import FakeConfig, _main

from ooptra_client import OoptraClient

try:
    import httpx
    from quart import Quart, g, request
except ImportError:
    Quart = None


@unittest.skipUnless(Quart, "Quart is an optional development dependency")
class TestWebRoutes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = Quart(__name__)

        @app.before_request
        async def identity():
            g.username = request.headers.get("X-Test-User")

        class Context:
            registered_web_apis = []

            def register_web_api(self, route, handler, methods, desc):
                self.registered_web_apis.append((route, handler, methods, desc))
                app.add_url_rule("/api/plug" + route, endpoint=route, view_func=handler, methods=methods)

        self.config = FakeConfig(api_base="http://localhost", api_token="secret", group_map={})
        self.plugin = _main().OoptraPlugin(Context(), self.config)
        self.requests = []

        def upstream(req):
            self.requests.append(req)
            if req.url.path == "/api/logs/stream":
                return httpx.Response(200, text='event: line\ndata: {"line":"connected"}\n\n', headers={"Content-Type": "text/event-stream"})
            return httpx.Response(200, json={"ok": True, "joined": False})

        self.plugin._client = OoptraClient("http://localhost", "secret", transport=httpx.MockTransport(upstream))
        self.client = app.test_client()
        self.app = app
        self.prefix = "/api/plug/astrbot_plugin_ooptra/ui/"
        self.auth = {"X-Test-User": "admin"}

    async def test_unauthenticated_writes_do_not_modify_config_or_call_ooptra(self):
        for endpoint, body in (("binding", {"group_id": "123456", "areas": ["AREA-A"]}),
                               ("action", {"action": "leave"}),
                               ("console", {"path": "/api/status"})):
            response = await self.client.post(self.prefix + endpoint, json=body)
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.config.saved, 0)
        self.assertEqual(self.requests, [])

    async def test_authenticated_binding_is_persisted_and_invalid_request_is_rejected(self):
        response = await self.client.post(self.prefix + "binding", headers=self.auth,
                                          json={"group_id": "123456", "areas": ["AREA-A", "AREA-B"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.config["group_map"]["123456"]["areas"], ["AREA-A", "AREA-B"])
        response = await self.client.post(self.prefix + "binding", headers=self.auth, json=[])
        self.assertEqual(response.status_code, 400)

    async def test_upstream_auth_failure_is_an_explicit_error(self):
        self.plugin._client._transport = httpx.MockTransport(lambda req: httpx.Response(401, json={"error": "wrong token"}))
        response = await self.client.get(self.prefix + "status", headers=self.auth)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret", await response.get_data(as_text=True))

    async def test_named_sse_events_stream_after_handler_returns(self):
        response = await self.client.get(self.prefix + "logs?file=oopz_bot.log&lines=10", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertIn('event: line\ndata: {"line":"connected"}\n\n', await response.get_data(as_text=True))
        self.assertEqual(dict(self.requests[0].url.params), {"file": "oopz_bot.log", "lines": "10"})

    async def test_backup_download_is_authenticated_and_validates_fixed_identifier(self):
        response = await self.client.get(self.prefix + "backup-download?id=" + "a" * 32)
        self.assertEqual(response.status_code, 401)
        response = await self.client.get(self.prefix + "backup-download?id=../config.py", headers=self.auth)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.requests, [])

        def backup(req):
            self.requests.append(req)
            return httpx.Response(200, content=b"PK\x03\x04test-backup", headers={"Content-Type": "application/zip"})

        self.plugin._client._transport = httpx.MockTransport(backup)
        response = await self.client.get(self.prefix + "backup-download?id=" + "a" * 32, headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(await response.get_data(), b"PK\x03\x04test-backup")
        self.assertEqual(self.requests[-1].url.path, "/api/maintenance/backups/" + "a" * 32)
        self.assertEqual(self.requests[-1].headers["Authorization"], "Bearer secret")
        await self.plugin.terminate()
        response = await self.client.get(self.prefix + "backup-download?id=" + "a" * 32, headers=self.auth)
        self.assertEqual(response.status_code, 410)

    async def test_termination_unregisters_only_own_routes_and_blocks_retained_handlers(self):
        other = ("/another", lambda: None, ["GET"], "another plugin")
        self.plugin.context.registered_web_apis.append(other)
        await self.plugin.terminate()
        self.assertEqual(self.plugin.context.registered_web_apis, [other])
        # Quart retains the handlers even after unregistering, exercising the lifecycle guard.
        for endpoint in ("bootstrap", "status", "logs", "logs-download"):
            response = await self.client.get(self.prefix + endpoint, headers=self.auth)
            self.assertEqual(response.status_code, 410)
        for endpoint, body in (("binding", {"group_id": "123456", "areas": ["AREA-A"]}),
                               ("action", {"action": "leave"}),
                               ("console", {"method": "POST", "path": "/api/bridge/restart"})):
            response = await self.client.post(self.prefix + endpoint, headers=self.auth, json=body)
            self.assertEqual(response.status_code, 410)
        self.assertEqual(self.config.saved, 0)
        self.assertEqual(self.requests, [])

    async def test_termination_cancels_open_log_stream_and_releases_upstream(self):
        started, released = asyncio.Event(), asyncio.Event()

        async def stream_logs(file, lines):
            try:
                started.set()
                yield "event: line\ndata: connected\n\n"
                await asyncio.Event().wait()
            finally:
                released.set()

        self.plugin._client.stream_logs = stream_logs
        handler = next(entry[1] for entry in self.plugin.context.registered_web_apis if entry[0].endswith("/logs"))
        async with self.app.test_request_context(self.prefix + "logs"):
            g.username = "admin"
            response = await handler()

        async def consume():
            async for _ in response.response:
                pass

        reader = asyncio.create_task(consume())
        await started.wait()
        await self.plugin.terminate()
        with self.assertRaises(asyncio.CancelledError):
            await reader
        self.assertTrue(released.is_set())
        self.assertEqual(self.plugin._webui_stream_tasks, set())


if __name__ == "__main__":
    unittest.main()

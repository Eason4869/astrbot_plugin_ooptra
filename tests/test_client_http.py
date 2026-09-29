"""ooptra_client 的 HTTP 行为单测（httpx.MockTransport，不打真实网络）。

这个文件存在的理由：原来的 tests/test_client_unit.py 只测纯函数，零 HTTP。
于是「路径写错」「trust_env 把请求交给系统代理」「非 dict 响应被伪造成成功」
这些问题在测试里完全看不见——而它们正是线上真实踩到的故障。
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - 插件运行环境必有 httpx
    httpx = None  # type: ignore[assignment]

requires_httpx = unittest.skipUnless(httpx is not None, "需要 httpx")

BASE = "http://127.0.0.1:3090"


def _responder(status: int, body: Any, content_type: str = "application/json"):
    """构造一个固定响应的 handler。body 为 dict/list 时按 JSON 序列化。"""

    def handler(request: Any) -> Any:
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body, request=request)
        content = body.encode("utf-8") if isinstance(body, str) else body
        return httpx.Response(
            status,
            content=content,
            headers={"Content-Type": content_type},
            request=request,
        )

    return handler


def _client(handler: Any, **kwargs: Any):
    from ooptra_client import OoptraClient

    return OoptraClient(BASE, transport=httpx.MockTransport(handler), **kwargs)


@requires_httpx
class TestRequestErrorBranches(unittest.TestCase):
    def _raises(self, client: Any, call: str, *args: Any):
        from ooptra_client import OoptraError

        with self.assertRaises(OoptraError) as ctx:
            asyncio.run(getattr(client, call)(*args))
        return ctx.exception

    def test_ok_false_surfaces_server_error_text(self):
        client = _client(_responder(200, {"ok": False, "error": "机器人尚未就绪"}))
        exc = self._raises(client, "status")
        self.assertIn("机器人尚未就绪", str(exc))

    def test_401_names_the_token_to_fill(self):
        client = _client(_responder(401, {"ok": False, "error": "nope"}))
        exc = self._raises(client, "status")
        self.assertEqual(exc.status_code, 401)
        self.assertIn("WEBUI_CONFIG.token", str(exc))

    def test_404_names_the_endpoint_and_the_version_hint(self):
        """P9：404 必须说清是哪个端点，并区分三种原因。"""
        client = _client(_responder(404, {"ok": False, "error": "not found"}))
        exc = self._raises(client, "channels", "AREA")
        self.assertEqual(exc.status_code, 404)
        self.assertIn("GET /voice/channels", str(exc))
        self.assertIn("2.0.0", str(exc))

    def test_500_reports_status_code_and_body(self):
        client = _client(_responder(500, {"ok": False, "error": "boom"}))
        exc = self._raises(client, "leave")
        self.assertEqual(exc.status_code, 500)
        self.assertIn("500", str(exc))

    def test_empty_body_is_an_error_not_a_success(self):
        """P3：空响应体曾被当成「成功且无数据」。"""
        client = _client(_responder(200, b""))
        exc = self._raises(client, "status")
        self.assertIn("空响应体", str(exc))

    def test_non_json_body_is_an_error(self):
        client = _client(_responder(200, "<html>proxy error</html>", "text/html"))
        exc = self._raises(client, "status")
        self.assertIn("不是 JSON", str(exc))

    def test_timeout_is_reported_readably(self):
        def handler(request: Any) -> Any:
            raise httpx.TimeoutException("timed out", request=request)

        exc = self._raises(_client(handler), "status")
        self.assertIn("超时", str(exc))

    def test_connect_error_is_reported_readably(self):
        def handler(request: Any) -> Any:
            raise httpx.ConnectError("connection refused", request=request)

        exc = self._raises(_client(handler), "status")
        self.assertIn("无法连接", str(exc))


@requires_httpx
class TestContractShape(unittest.TestCase):
    def _raises(self, client: Any, call: str, *args: Any):
        from ooptra_client import OoptraError

        with self.assertRaises(OoptraError) as ctx:
            asyncio.run(getattr(client, call)(*args))
        return ctx.exception

    def test_join_non_dict_is_not_faked_as_success(self):
        """P2：旧版对非 dict 响应返回 {"ok": True, "raw": …}——伪造进房成功。"""
        client = _client(_responder(200, ["unexpected"]))
        exc = self._raises(client, "join", "AREA", "CHAN")
        self.assertIn("符合契约", str(exc))

    def test_leave_non_dict_is_not_faked_as_success(self):
        client = _client(_responder(200, [1]))
        exc = self._raises(client, "leave")
        self.assertIn("符合契约", str(exc))

    def test_status_non_dict_is_not_wrapped_in_raw(self):
        """P4：{"raw": …} 没有任何 formatter 读，等于把故障显示成空数据。"""
        client = _client(_responder(200, [1, 2, 3]))
        exc = self._raises(client, "status")
        self.assertIn("符合契约", str(exc))

    def test_members_non_dict_is_rejected(self):
        client = _client(_responder(200, "42"))  # 合法 JSON，但不是对象
        self.assertIn("符合契约", str(self._raises(client, "members", "A", "C")))


@requires_httpx
class TestHealthFallback(unittest.TestCase):
    """P5：回退条件必须收窄到「服务不认识 /health」，并标明数据来自哪个端点。"""

    def _routes(self, mapping: dict[str, Any]):
        def handler(request: Any) -> Any:
            path = request.url.path
            if path in mapping:
                status, body = mapping[path]
                if isinstance(body, Exception):
                    raise body
                return httpx.Response(status, json=body, request=request)
            return httpx.Response(404, json={"ok": False, "error": "nf"}, request=request)

        return handler

    def _client_for(self, mapping: dict[str, Any]):
        return _client(self._routes(mapping))

    def test_prefers_health_and_marks_via(self):
        client = self._client_for(
            {
                "/health": (200, {"ok": True, "service": "ooptra-voice-api", "version": "2.0.0"}),
                "/voice/status": (200, {"ok": True, "joined": True}),
            }
        )
        data = asyncio.run(client.health())
        self.assertEqual(data["via"], "/health")
        self.assertEqual(data["version"], "2.0.0")

    def test_falls_back_on_404_and_marks_via(self):
        client = self._client_for(
            {
                "/health": (404, {"ok": False, "error": "nf"}),
                "/voice/status": (200, {"ok": True, "joined": False}),
            }
        )
        data = asyncio.run(client.health())
        self.assertEqual(data["via"], "/voice/status")
        self.assertIs(data["joined"], False)

    def test_falls_back_on_405_and_501(self):
        for status in (405, 501):
            with self.subTest(status=status):
                client = self._client_for(
                    {
                        "/health": (status, {"ok": False, "error": "no"}),
                        "/voice/status": (200, {"ok": True, "joined": False}),
                    }
                )
                self.assertEqual(asyncio.run(client.health())["via"], "/voice/status")

    def test_500_does_not_fall_back(self):
        """服务真挂了就该立刻报错，而不是白等第二个 timeout。"""
        from ooptra_client import OoptraError

        client = self._client_for(
            {
                "/health": (500, {"ok": False, "error": "boom"}),
                "/voice/status": (200, {"ok": True, "joined": False}),
            }
        )
        with self.assertRaises(OoptraError) as ctx:
            asyncio.run(client.health())
        self.assertEqual(ctx.exception.status_code, 500)

    def test_401_does_not_fall_back(self):
        from ooptra_client import OoptraError

        client = self._client_for(
            {
                "/health": (401, {"ok": False, "error": "bad token"}),
                "/voice/status": (200, {"ok": True, "joined": False}),
            }
        )
        with self.assertRaises(OoptraError) as ctx:
            asyncio.run(client.health())
        self.assertEqual(ctx.exception.status_code, 401)

    def test_timeout_does_not_fall_back(self):
        from ooptra_client import OoptraError

        def handler(request: Any) -> Any:
            raise httpx.TimeoutException("timed out", request=request)

        with self.assertRaises(OoptraError) as ctx:
            asyncio.run(_client(handler).health())
        self.assertIn("超时", str(ctx.exception))


@requires_httpx
class TestTransportHardening(unittest.TestCase):
    def test_trust_env_is_disabled(self):
        """P1：httpx 不会自动绕过 localhost 代理，必须显式 trust_env=False。

        开着 trust_env 时，系统里的 HTTP_PROXY 会把 127.0.0.1 的请求也劫走：
        Ooptra 明明正常却报「无法连接」，且 Bearer token 与请求体被交给代理。
        """
        seen: list[dict[str, Any]] = []
        real_client = httpx.AsyncClient  # patch 前先抓住真类，否则 factory 会递归调自己

        def factory(**kwargs: Any) -> Any:
            seen.append(dict(kwargs))
            kwargs.pop("transport", None)
            return real_client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, json={"ok": True, "joined": False}, request=request
                    )
                ),
                **kwargs,
            )

        from ooptra_client import OoptraClient

        client = OoptraClient(BASE)  # 不注入 transport，走真实构造分支
        with mock.patch("httpx.AsyncClient", factory):
            asyncio.run(client.status())

        self.assertTrue(seen, "没有走到 httpx.AsyncClient 构造")
        self.assertIs(seen[0].get("trust_env"), False)

    def test_authorization_header_sent_only_with_token(self):
        captured: list[dict[str, str]] = []

        def handler(request: Any) -> Any:
            captured.append(dict(request.headers))
            return httpx.Response(200, json={"ok": True, "joined": False}, request=request)

        asyncio.run(_client(handler).status())
        self.assertNotIn("authorization", {k.lower() for k in captured[0]})

        asyncio.run(_client(handler, token="s3cret").status())
        self.assertEqual(captured[1].get("authorization"), "Bearer s3cret")

    def test_missing_base_url_is_reported(self):
        from ooptra_client import OoptraClient, OoptraError

        with self.assertRaises(OoptraError) as ctx:
            asyncio.run(OoptraClient("").status())
        self.assertIn("api_base", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

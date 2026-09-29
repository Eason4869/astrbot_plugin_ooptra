"""main.py 指令层单测：跨域保护、锁释放时机、自检契约探测、返回文案。

main.py 依赖 astrbot 运行时，这里先用假模块把它导进来。插件真正出问题的地方
（多域群里选错域、跨域进房、持锁 yield、返回一坨 dict）都在这一层，不测等于没测。
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import logging
import sys
import types
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))

from ooptra_client import OoptraError

# ----------------------------------------------------------------------
# 假 astrbot：只提供 main.py 用到的那点接口
# ----------------------------------------------------------------------


def _install_fake_astrbot() -> None:
    if "astrbot.api.event" in sys.modules:
        return

    def decorator(*args: Any, **kwargs: Any) -> Any:
        if len(args) == 1 and not kwargs and callable(args[0]):
            return args[0]

        def wrap(fn: Any) -> Any:
            return fn

        return wrap

    api = types.ModuleType("astrbot.api")
    api.AstrBotConfig = dict
    api.logger = logging.getLogger("astrbot.test")

    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = object
    event.filter = types.SimpleNamespace(
        PermissionType=types.SimpleNamespace(ADMIN="admin", MEMBER="member"),
        command=decorator,
        llm_tool=decorator,
        permission_type=decorator,
    )

    star = types.ModuleType("astrbot.api.star")
    star.Context = object

    class Star:
        def __init__(self, context: Any = None):
            self.context = context

    star.Star = Star

    package = types.ModuleType("astrbot")
    package.__path__ = []  # type: ignore[attr-defined]
    api.event = event
    api.star = star
    package.api = api

    sys.modules.update(
        {
            "astrbot": package,
            "astrbot.api": api,
            "astrbot.api.event": event,
            "astrbot.api.star": star,
        }
    )


_MAIN: Any = None


def _main() -> Any:
    """按「包内相对导入」的方式加载 main.py（与 AstrBot 的加载方式一致）。"""
    global _MAIN
    if _MAIN is not None:
        return _MAIN
    _install_fake_astrbot()
    pkg_name = "ooptra_plugin_under_test"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    sys.modules[pkg_name] = pkg
    # 让 main.py 的 `from .ooptra_client import ...` 复用测试已经导入的那个模块对象。
    # 否则会出现两份 OoptraError 类，main.py 里的 except 匹配不上测试抛的异常。
    sys.modules[f"{pkg_name}.ooptra_client"] = sys.modules["ooptra_client"]
    spec = importlib.util.spec_from_file_location(f"{pkg_name}.main", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _MAIN = module
    return module


# ----------------------------------------------------------------------
# 测试替身
# ----------------------------------------------------------------------


class FakeConfig(dict):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.saved = 0

    def save_config(self) -> None:
        self.saved += 1


class FakeEvent:
    def __init__(self, group_id: str = "123456", admin: bool = False) -> None:
        self.message_obj = types.SimpleNamespace(group_id=group_id)
        self._admin = admin

    def is_admin(self) -> bool:
        return self._admin

    def plain_result(self, text: str) -> str:
        return text


class FakeClient:
    """只实现插件用到的几个方法；可用构造参数指定返回的错误。"""

    # 与默认配置一致，避免插件的 client property 重建真实客户端
    base_url = "http://127.0.0.1:3090"
    token = ""
    timeout = 8.0

    def __init__(
        self,
        *,
        status: dict[str, Any] | None = None,
        channels: Any = None,
        members: Any = None,
        health: Any = None,
        join_error: Exception | None = None,
    ) -> None:
        self._status = status if status is not None else {
            "ok": True,
            "joined": False,
            "default_area": "",
            "default_channel": "",
        }
        self._channels = channels if channels is not None else {"ok": True, "channels": []}
        self._members = members if members is not None else {"ok": True, "members": [], "count": 0}
        self._health = health if health is not None else {
            "ok": True,
            "service": "ooptra-voice-api",
            "version": "2.0.0",
            "enabled": True,
            "via": "/health",
        }
        self._join_error = join_error
        self.joined: list[tuple[str, str]] = []
        self.left = 0

    @staticmethod
    def _maybe_raise(value: Any) -> Any:
        if isinstance(value, Exception):
            raise value
        return dict(value)

    async def status(self) -> dict[str, Any]:
        return self._maybe_raise(self._status)

    async def channels(self, area: str) -> dict[str, Any]:
        return self._maybe_raise(self._channels)

    async def members(self, area: str, channel: str) -> dict[str, Any]:
        return self._maybe_raise(self._members)

    async def health(self) -> dict[str, Any]:
        return self._maybe_raise(self._health)

    async def join(self, area: str, channel: str) -> dict[str, Any]:
        if self._join_error:
            raise self._join_error
        self.joined.append((area, channel))
        return {"ok": True, "joined": True, "area": area, "channel": channel}

    async def leave(self) -> dict[str, Any]:
        self.left += 1
        return {"ok": True, "joined": False}


def _plugin(**config: Any) -> tuple[Any, FakeConfig]:
    main = _main()
    cfg = FakeConfig(
        api_base="http://127.0.0.1:3090",
        api_token="",
        timeout_sec=8.0,
        group_map={},
        allow_join=True,
        join_admin_only=False,
        allow_arbitrary_channel=False,
        enable_llm_tools=True,
    )
    cfg.update(config)
    plugin = main.OoptraPlugin(context=None, config=cfg)
    return plugin, cfg


def _attach(plugin: Any, **kwargs: Any) -> FakeClient:
    client = FakeClient(**kwargs)
    plugin._client = client
    return client


async def _first_line(gen: Any) -> Any:
    item = await gen.__anext__()
    with contextlib.suppress(Exception):
        await gen.aclose()
    return item


def _run_first(gen: Any) -> Any:
    return asyncio.run(_first_line(gen))


# ----------------------------------------------------------------------
# 跨域保护（P6）
# ----------------------------------------------------------------------


class TestCrossAreaGuard(unittest.TestCase):
    def test_default_channel_is_dropped_when_area_differs(self) -> None:
        plugin, _ = _plugin(group_map={"123456": {"area": "AREA-B"}})
        _attach(
            plugin,
            status={
                "ok": True,
                "joined": False,
                "default_area": "AREA-A",
                "default_channel": "CHAN-A",
            },
        )
        mapping = asyncio.run(plugin._require_mapping(FakeEvent()))
        self.assertEqual(mapping["area"], "AREA-B")
        self.assertEqual(mapping["_default_area"], "AREA-A")
        self.assertEqual(mapping["_default_channel"], "", "跨域默认频道必须被丢弃")

    def test_default_channel_is_kept_when_area_matches(self) -> None:
        plugin, _ = _plugin(group_map={"123456": {"area": "AREA-A"}})
        _attach(
            plugin,
            status={
                "ok": True,
                "joined": False,
                "default_area": "AREA-A",
                "default_channel": "CHAN-A",
            },
        )
        mapping = asyncio.run(plugin._require_mapping(FakeEvent()))
        self.assertEqual(mapping["_default_channel"], "CHAN-A")

    def test_join_target_rejects_default_channel_outside_area(self) -> None:
        plugin, _ = _plugin(group_map={"123456": {"area": "AREA-B"}})
        _attach(plugin, channels={"ok": True, "channels": [{"id": "CHAN-B", "name": "乙房"}]})
        mapping = {
            "area": "AREA-B",
            "channel": "",
            "label": "",
            "_default_channel": "CHAN-A",
        }
        _area, cid, _name, err = asyncio.run(
            plugin._resolve_join_target(FakeEvent(), mapping, "")
        )
        self.assertIsNone(cid)
        self.assertIn("跨域", err)

    def test_join_target_resolves_default_channel_within_area(self) -> None:
        plugin, _ = _plugin(group_map={"123456": {"area": "AREA-B"}})
        _attach(
            plugin,
            channels={
                "ok": True,
                "channels": [
                    {"id": "CHAN-B", "name": "乙房", "count": 0},
                    {"id": "CHAN-C", "name": "丙房", "count": 2},
                ],
            },
        )
        mapping = {
            "area": "AREA-B",
            "channel": "",
            "label": "",
            "_default_channel": "CHAN-C",
        }
        area, cid, name, err = asyncio.run(
            plugin._resolve_join_target(FakeEvent(), mapping, "")
        )
        self.assertIsNone(err)
        self.assertEqual((area, cid, name), ("AREA-B", "CHAN-C", "丙房"))


# ----------------------------------------------------------------------
# 多域群：LLM 工具与斜杠命令必须选同一个域（P7）
# ----------------------------------------------------------------------


class TestPreferredAreaConsistency(unittest.TestCase):
    def test_resolve_area_channel_uses_ooptra_default_area(self) -> None:
        plugin, _ = _plugin(
            group_map={"123456": {"areas": ["AREA-1", "AREA-2"], "channel": "CHAN-2"}}
        )
        _attach(
            plugin,
            status={
                "ok": True,
                "joined": False,
                "default_area": "AREA-2",
                "default_channel": "CHAN-2",
            },
        )
        area, channel = asyncio.run(plugin._resolve_area_channel(FakeEvent(), "", ""))
        self.assertEqual((area, channel), ("AREA-2", "CHAN-2"))


# ----------------------------------------------------------------------
# 返回文案与锁（P8 + 简化提示）
# ----------------------------------------------------------------------


class TestCommandReplies(unittest.TestCase):
    def test_join_reply_is_one_short_line(self) -> None:
        plugin, _ = _plugin(
            group_map={"123456": {"area": "AREA-B", "channel": "CHAN-B", "label": "开黑房"}}
        )
        client = _attach(plugin)
        text = _run_first(plugin.voice_join(FakeEvent()))
        self.assertEqual(text, "已进语音：开黑房")
        self.assertEqual(client.joined, [("AREA-B", "CHAN-B")])

    def test_leave_reply_is_one_short_line(self) -> None:
        plugin, _ = _plugin()
        client = _attach(plugin)
        text = _run_first(plugin.voice_leave(FakeEvent()))
        self.assertEqual(text, "已退出语音。")
        self.assertEqual(client.left, 1)

    def test_join_failure_reports_error_without_success_text(self) -> None:
        plugin, _ = _plugin(group_map={"123456": {"area": "A", "channel": "C"}})
        _attach(plugin, join_error=OoptraError("Oopz bot 尚未就绪"))
        text = _run_first(plugin.voice_join(FakeEvent()))
        self.assertIn("调用 Ooptra 失败", text)
        self.assertIn("尚未就绪", text)
        self.assertNotIn("已进语音", text)

    def test_voice_op_lock_is_released_before_yield(self) -> None:
        """进房失败分支的 yield 若留在 async with 里，生成器挂起时锁一直被握着——
        这期间并发的 /退语音 会全部阻塞（成功分支的 yield 本来就在锁外，测不到）。"""
        plugin, _ = _plugin(
            group_map={"123456": {"area": "A", "channel": "C", "label": "开黑房"}}
        )
        _attach(plugin, join_error=OoptraError("Oopz bot 尚未就绪"))

        async def run() -> tuple[Any, bool]:
            gen = plugin.voice_join(FakeEvent())
            item = await gen.__anext__()
            held = plugin._voice_op_lock.locked()
            with contextlib.suppress(Exception):
                await gen.aclose()
            return item, held

        item, held = asyncio.run(run())
        self.assertIn("调用 Ooptra 失败", item)
        self.assertFalse(held, "yield 时仍持有语音操作锁")

    def test_join_is_blocked_when_admin_only_and_not_admin(self) -> None:
        plugin, _ = _plugin(
            join_admin_only=True,
            group_map={"123456": {"area": "A", "channel": "C"}},
        )
        client = _attach(plugin)
        text = _run_first(plugin.voice_join(FakeEvent(admin=False)))
        self.assertIn("不允许进语音", text)
        self.assertEqual(client.joined, [])


# ----------------------------------------------------------------------
# /语音自检 的契约探测（P11）
# ----------------------------------------------------------------------


class TestSelfCheck(unittest.TestCase):
    def test_ping_lists_all_contract_probes(self) -> None:
        plugin, _ = _plugin(group_map={"123456": {"area": "AREA-A"}})
        _attach(plugin)
        text = _run_first(plugin.voice_ping(FakeEvent()))
        for label in (
            "GET /health",
            "GET /voice/status",
            "GET /voice/channels",
            "GET /voice/members",
        ):
            self.assertIn(f"✅ {label}", text)

    def test_ping_reports_missing_channels_endpoint(self) -> None:
        plugin, _ = _plugin(group_map={"123456": {"area": "AREA-A"}})
        _attach(
            plugin,
            channels=OoptraError("Ooptra 未提供接口 GET /voice/channels（需 ≥ 2.0.0）"),
        )
        text = _run_first(plugin.voice_ping(FakeEvent()))
        self.assertIn("❌ GET /voice/channels", text)
        self.assertIn("2.0.0", text)

    def test_ping_skips_channel_probes_without_binding(self) -> None:
        plugin, _ = _plugin()
        _attach(plugin)
        text = _run_first(plugin.voice_ping(FakeEvent()))
        self.assertNotIn("GET /voice/channels", text)
        self.assertIn("未绑定域", text)

    def test_ping_reports_unreachable_service(self) -> None:
        plugin, _ = _plugin()
        _attach(plugin, health=OoptraError("无法连接 Ooptra VOICE_API：refused"))
        text = _run_first(plugin.voice_ping(FakeEvent()))
        self.assertIn("❌ 无法访问", text)
        self.assertIn("WEBUI_CONFIG.token", text)

    def test_ping_warns_when_voice_disabled_on_ooptra(self) -> None:
        plugin, _ = _plugin()
        _attach(
            plugin,
            health={"ok": True, "service": "ooptra-voice-api", "enabled": False, "via": "/health"},
        )
        text = _run_first(plugin.voice_ping(FakeEvent()))
        self.assertIn("voice.enabled=false", text)


if __name__ == "__main__":
    unittest.main()

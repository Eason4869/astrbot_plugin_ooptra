"""Thin adapters for AstrBot's current Web API and legacy Quart Dashboard."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .ooptra_client import OoptraError
from .webui import PanelInactive

PREFIX = "/astrbot_plugin_ooptra/ui"


async def unregister_panel(plugin: Any) -> None:
    registry = getattr(plugin.context, "registered_web_apis", None)
    if isinstance(registry, list):
        registry[:] = [entry for entry in registry if entry[1] not in plugin._webui_handlers]
    plugin._webui_handlers.clear()
    tasks = [task for task in plugin._webui_stream_tasks if task is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    plugin._webui_stream_tasks.clear()


def register_panel(plugin: Any) -> None:
    context = plugin.context
    if context is None or not callable(getattr(context, "register_web_api", None)):
        return
    try:
        from astrbot.api.web import (
            error_response,
            json_response,
            request,
            stream_response,
        )
        from starlette.responses import Response

        def username():
            return request.username

        async def payload():
            return await request.json(default={})

        def query():
            return dict(request.query.items())

    except ImportError:
        try:
            from quart import Response, g, jsonify, request
        except ImportError:
            from astrbot.api import logger
            logger.info("Ooptra 插件页面需要支持 Plugin Pages/Views 的 AstrBot；QQ 命令仍可使用。")
            return

        def username():
            return g.get("username")

        async def payload():
            return await request.get_json(silent=True)

        def query():
            return dict(request.args.items())

        def json_response(data):
            return jsonify(data)

        def error_response(message, status_code=400):
            return jsonify({"status": "error", "message": message}), status_code

        def stream_response(events):
            return Response(events, content_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    panel = plugin.panel

    def register(name, handler, method, description):
        context.register_web_api(f"{PREFIX}/{name}", handler, [method], description)
        plugin._webui_handlers.add(handler)

    def route(name, method, operation):
        async def handler():
            if not username():
                return error_response("请先登录 AstrBot 管理面板。", status_code=401)
            try:
                panel.ensure_active()
                arguments = await payload() if method == "POST" else query()
                result = await operation(arguments)
                return json_response(result)
            except PanelInactive as exc:
                return error_response(str(exc), status_code=410)
            except ValueError as exc:
                return error_response(str(exc), status_code=400)
            except OoptraError as exc:
                return error_response(str(exc), status_code=502)
            except OSError:
                return error_response("保存配置失败，请检查目录权限和磁盘空间。", status_code=500)

        register(name, handler, method, f"Ooptra control panel: {name}")

    async def bootstrap(_):
        return panel.bootstrap()

    async def status(_):
        return await panel.status()

    async def console_page(_):
        return await panel.console_page()

    async def areas(_):
        return await panel.areas()

    async def channels(args):
        return await panel.channels(args.get("area", ""))

    async def members(args):
        return await panel.members(args.get("area", ""), args.get("channel", ""))

    for name, operation in (("bootstrap", bootstrap), ("status", status), ("areas", areas),
                            ("channels", channels), ("members", members), ("console-page", console_page)):
        route(name, "GET", operation)
    for name, operation in (("action", panel.action), ("binding", panel.save_binding),
                            ("unbind", panel.delete_binding), ("console", panel.console)):
        route(name, "POST", operation)

    async def logs():
        if not username():
            return error_response("请先登录 AstrBot 管理面板。", status_code=401)
        if not plugin._webui_active:
            return error_response("Ooptra 插件已停用。", status_code=410)
        arguments = query()

        async def events():
            task = asyncio.current_task()
            plugin._webui_stream_tasks.add(task)
            try:
                async for chunk in panel.logs(arguments):
                    yield chunk
            except (ValueError, OoptraError) as exc:
                yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"
            finally:
                plugin._webui_stream_tasks.discard(task)

        return stream_response(events())

    async def download_logs():
        if not username():
            return error_response("请先登录 AstrBot 管理面板。", status_code=401)
        try:
            panel.ensure_active()
            result = await panel.console({"method": "GET", "path": "/api/logs/tail", "params": query()})
            return Response("\n".join(result.get("lines", [])), media_type="text/plain") if Response.__module__.startswith("starlette") else Response("\n".join(result.get("lines", [])), content_type="text/plain")
        except PanelInactive as exc:
            return error_response(str(exc), status_code=410)
        except (ValueError, OoptraError) as exc:
            return error_response(str(exc), status_code=502)

    register("logs", logs, "GET", "Ooptra console log stream")
    register("logs-download", download_logs, "GET", "Ooptra console log download")

    async def download_backup():
        if not username():
            return error_response("请先登录 AstrBot 管理面板。", status_code=401)
        try:
            backup_id = query().get("id", "")
            content = await panel.backup(backup_id)
            headers = {"Content-Disposition": f'attachment; filename="ooptra-{backup_id}.zip"', "Cache-Control": "no-store"}
            if Response.__module__.startswith("starlette"):
                return Response(content, media_type="application/zip", headers=headers)
            return Response(content, content_type="application/zip", headers=headers)
        except PanelInactive as exc:
            return error_response(str(exc), status_code=410)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        except OoptraError as exc:
            return error_response(str(exc), status_code=502)

    register("backup-download", download_backup, "GET", "Ooptra console backup download")

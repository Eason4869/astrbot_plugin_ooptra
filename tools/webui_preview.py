"""Development-only host: real AstrBot bridge/API, mocked Ooptra, no user config.

python tools/webui_preview.py /path/to/AstrBot --port 18765
Requires optional dev dependencies: fastapi, hypercorn, httpx.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib.util
import json
import re
import sys
import types
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlencode, urlunsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from hypercorn.asyncio import serve
from hypercorn.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_main_logic import (  # noqa: E402 -- load the test runtime explicitly
    FakeConfig,
    _main,
)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_page_renderer(source: Path):
    """Use AstrBot's actual pure HTML processor without loading the bot runtime."""
    module = ast.parse(source.read_text(encoding="utf-8"))
    service = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "PluginPageService")
    names = {"apply_theme_to_html", "get_plugin_page_bridge_sdk_url", "process_plugin_page_html"}
    methods = [node for node in service.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in methods} != names:
        raise RuntimeError("AstrBot HTML processor changed; update the preview loader.")
    pattern = next(node for node in module.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "_HTML_ASSET_ATTR_RE" for target in node.targets))
    extracted = ast.Module(body=[pattern, ast.ClassDef(name="PluginPageService", bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])
    namespace = {"re": re, "urlencode": urlencode, "urlunsplit": urlunsplit}
    exec(compile(ast.fix_missing_locations(extracted), str(source), "exec"), namespace)
    return namespace["PluginPageService"]()


def build_app(astrbot_source: Path):
    renderer = load_page_renderer(astrbot_source / "astrbot/dashboard/services/plugin_page_service.py")
    main = _main()  # Substitute only AstrBot's non-web plugin runtime.
    for name in ("astrbot.core", "astrbot.core.utils"):
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package
    load_module("astrbot.core.utils.upload", astrbot_source / "astrbot/core/utils/upload.py")
    web = load_module("astrbot.api.web", astrbot_source / "astrbot/api/web.py")
    from ooptra_client import OoptraClient

    app = FastAPI()
    routes = {}

    class Context:
        def register_web_api(self, route, handler, methods, desc):
            for method in methods:
                routes[(method, route)] = handler

    class PreviewConfig(FakeConfig):
        def save_config(self):
            super().save_config()
            destination = ROOT / ".webui-evidence/preview-config.json"
            destination.parent.mkdir(exist_ok=True)
            destination.write_text(json.dumps(self, ensure_ascii=False), encoding="utf-8")

    config = PreviewConfig(api_base="http://preview.invalid", api_token="preview-server-only-token", timeout_sec=8,
        group_map={"123456789": {"area": "demo-area", "channel": "demo-channel", "label": "开黑群"},
                   "987654321": {"areas": ["demo-area", "demo-area-2"], "channel": "", "label": "多域交流群"}})
    plugin = main.OoptraPlugin(Context(), config)
    state = {"ok": True, "joined": True, "enabled": True, "backend": "gemini_live", "state": "listening",
             "area": "demo-area", "channel": "demo-channel", "default_area": "demo-area", "default_channel": "demo-channel"}
    calls = []
    failure = {"offline": False}
    initial_config = deepcopy(dict(config))
    initial_state = dict(state)

    def upstream(request):
        calls.append({"method": request.method, "path": request.url.path,
                      "body": json.loads(request.content) if request.content else None})
        if request.headers.get("Authorization") != "Bearer preview-server-only-token":
            return httpx.Response(401, json={"ok": False})
        if failure["offline"]:
            return httpx.Response(503, json={"ok": False, "error": "Ooptra 测试服务已离线"})
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == "/api/status":
            data = {"ok": True, "process": {"version": "3.0.2", "pid": 302, "python": "3.12", "platform": "preview"},
                "bridge": {"runtime": {"running": True, "uptime_seconds": 3600},
                    "oopz": {"connected": True, "nickname": "Ooptra", "self_uid": "preview-bot", "target": "Oopz", "joined_areas": 2},
                    "onebot": {"connected": True, "target": "AstrBot"}, "traffic": {"events_total": 42, "events_by_type": {"message": 42}},
                    "recent_events": [], "recent_actions": []},
                "config": {"voice": {"enabled": True, "backend": state["backend"], "status": state},
                    "oopz": {"default_area": "demo-area", "default_channel": "demo-channel"}}}
        elif path == "/api/credentials":
            data = {"ok": True, "credentials": {"has_password": True, "jwt_token": "masked", "person_uid": "preview-bot", "device_id": "preview-device", "login_phone": "138****0000"}}
        elif path == "/api/logs/stream":
            return httpx.Response(200, text='event: reset\ndata: {}\n\nevent: line\ndata: {"line":"[INFO] AstrBot console bridge connected"}\n\n', headers={"content-type": "text/event-stream"})
        elif path == "/api/logs":
            data = {"ok": True, "files": [{"name": "oopz_bot.log", "size": 1024}], "default": "oopz_bot.log"}
        elif path == "/api/logs/tail":
            data = {"ok": True, "lines": ["[INFO] AstrBot console bridge connected"]}
        elif path == "/api/config" and request.method == "GET":
            data = {"ok": True, "path": "preview/config.py", "groups": {
                "onebot": {"title": "OneBot v11 连接", "fields": {"enabled": {"type": "bool", "label": "启用 OneBot", "tier": "basic", "value": True}}},
                "voice": {"title": "语音对话 Agent", "fields": {"backend": {"type": "str", "label": "语音方案", "tier": "basic", "value": state["backend"]}}}}}
        elif path == "/api/config":
            updates = body.get("updates", {})
            state["backend"] = updates.get("voice", {}).get("backend", state["backend"])
            data = {"ok": True, "changed": {key: list(value) for key, value in updates.items()}, "hot_reloaded_fields": ["backend"], "notes": [], "restart_required": False}
        elif path == "/api/bridge/restart":
            data = {"ok": True}
        else:
            plain = path.removeprefix("/api")
            if plain == "/voice/status":
                data = {**state, "status": dict(state)}
            elif plain == "/oopz/areas":
                data = {"ok": True, "areas": [{"id": "demo-area", "name": "开黑基地"}, {"id": "demo-area-2", "name": "朋友的小岛"}], "default_area": "demo-area", "default_channel": "demo-channel"}
            elif plain in {"/voice/channels", "/oopz/channels"}:
                channels = [{"id": "demo-channel", "name": "开黑房", "count": 2}, {"id": "demo-channel-2", "name": "闲聊房", "count": 1}]
                if request.url.params.get("area") == "demo-area-2":
                    channels = [{"id": "other-channel", "name": "二域房", "count": 0}]
                data = {"ok": True, "channels": channels, "default_area": "demo-area", "default_channel": "demo-channel"}
            elif plain == "/voice/members":
                data = {"ok": True, "count": 2, "joined": True, "members": [{"uid": "u1", "name": "小明", "mic": True, "speaker": True, "mic_muted": False, "speaker_muted": False},
                    {"uid": "u2", "name": "小红", "mic": None, "speaker": None, "mic_muted": None, "speaker_muted": None}], "live_state_received": 1}
            elif plain == "/voice/join":
                state.update(joined=True, area=body["area"], channel=body["channel"])
                data = dict(state)
            elif plain == "/voice/leave":
                state["joined"] = False
                data = dict(state)
            elif plain == "/voice/auto-visit/config":
                areas = body["updates"]["areas"]
                data = {"ok": True, "changed": {"auto_visit": ["areas"]}, "config": {"areas": areas}, "status": {"areas": areas, "paused": False}, "notes": []}
            elif plain == "/voice/auto-visit":
                data = {"ok": True, "config": {"areas": {}, "defaults": {}}, "status": {"areas": {}, "paused": False, "events": []}}
            elif plain == "/persona":
                data = {"ok": True, "persona": body.get("persona", "你是一个友好的语音助手。")}
            elif plain == "/memory":
                data = {"ok": True, "messages": [], "removed": 0}
            else:
                return httpx.Response(404, json={"ok": False, "error": "preview route missing: " + path})
        return httpx.Response(200, json=data)

    plugin._client = OoptraClient(config["api_base"], config["api_token"], transport=httpx.MockTransport(upstream))

    @app.api_route("/api/v1/plugins/extensions/{route:path}", methods=["GET", "POST"])
    async def extension(route: str, request: Request):
        handler = routes.get((request.method, "/" + route))
        if handler is None:
            return Response(status_code=404)
        with web.bind_request_context(web.PluginRequest(request, username=request.headers.get("X-Test-User"), plugin_name="astrbot_plugin_ooptra")):
            return await handler()

    @app.get("/api/plugin/page/bridge-sdk.js")
    async def sdk():
        return Response((astrbot_source / "astrbot/dashboard/plugin_page_bridge.js").read_text(encoding="utf-8"), media_type="application/javascript", headers={"Access-Control-Allow-Origin": "*"})

    @app.get("/view/{path:path}")
    async def view(path: str):
        root = ROOT / "pages/control"
        file = (root / path).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            return Response(status_code=404)
        content = file.read_bytes()
        mime = {".html": "text/html", ".css": "text/css", ".js": "application/javascript", ".svg": "image/svg+xml"}.get(file.suffix, "text/plain")
        if file.suffix == ".html":
            content = renderer.process_plugin_page_html(content.decode("utf-8"), theme="light").encode("utf-8")
        return Response(content, media_type=mime, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})

    @app.get("/test/state")
    async def test_state():
        return {"config": config, "calls": calls, "state": state}

    @app.post("/test/reset")
    async def test_reset():
        config.clear()
        config.update(deepcopy(initial_config))
        config.saved = 0
        state.clear()
        state.update(initial_state)
        failure["offline"] = False
        calls.clear()
        plugin._last_voice_op_at = 0.0
        return {"ok": True}

    @app.post("/test/failure")
    async def test_failure(request: Request):
        failure.update(await request.json())
        return failure

    @app.get("/")
    async def host():
        return HTMLResponse(HOST)

    return app


HOST = '''<!doctype html><html><head><meta charset="utf-8"><title>AstrBot plugin sandbox test host</title></head>
<body style="margin:0"><iframe title="Ooptra plugin" src="/view/index.html" sandbox="allow-scripts allow-forms allow-downloads" style="width:100%;height:100vh;border:0"></iframe>
<script>
const frame=document.querySelector('iframe'); const streams=new Map();
const send=body=>frame.contentWindow.postMessage({channel:'astrbot-plugin-page',...body},'*');
window.addEventListener('message',async event=>{
  if(event.source!==frame.contentWindow || event.data?.channel!=='astrbot-plugin-page')return;
  const m=event.data;
  if(m.kind==='ready'){send({kind:'context',context:{pluginName:'astrbot_plugin_ooptra',isDark:false,locale:'zh-CN',i18n:{}}});return;}
  if(m.kind!=='request')return;
  try{
    const url=new URL('/api/v1/plugins/extensions/astrbot_plugin_ooptra/'+m.endpoint,location.origin);
    Object.entries(m.params||{}).forEach(([k,v])=>url.searchParams.set(k,v));
    if(m.action==='sse:unsubscribe'){streams.get(m.subscriptionId)?.abort();streams.delete(m.subscriptionId);send({kind:'response',requestId:m.requestId,ok:true,data:{}});return;}
    const controller=new AbortController();
    const res=await fetch(url,{method:m.action==='api:post'?'POST':'GET',headers:{'Content-Type':'application/json','X-Test-User':'admin'},body:m.action==='api:post'?JSON.stringify(m.body):undefined,signal:controller.signal});
    if(m.action==='sse:subscribe'){
      streams.set(m.subscriptionId,controller);send({kind:'response',requestId:m.requestId,ok:true,data:{}});
      const reader=res.body.getReader(),decoder=new TextDecoder();let pending='';
      while(true){const {value,done}=await reader.read();if(done)break;pending+=decoder.decode(value,{stream:true});let i;
        while((i=pending.indexOf('\\n\\n'))>=0){const block=pending.slice(0,i);pending=pending.slice(i+2);let type='message',data=[];
          block.split('\\n').forEach(line=>{if(line.startsWith('event:'))type=line.slice(6).trim();if(line.startsWith('data:'))data.push(line.slice(5).trimStart());});
          send({kind:'sse_message',subscriptionId:m.subscriptionId,eventType:type,data:data.join('\\n')});}}
      return;
    }
    if(m.action==='files:download'){const blob=await res.blob();const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=m.filename||'logs.txt';a.click();send({kind:'response',requestId:m.requestId,ok:true,data:{filename:a.download}});return;}
    const data=await res.json();send({kind:'response',requestId:m.requestId,ok:res.ok,data,error:data.message||data.error});
  }catch(error){send({kind:'response',requestId:m.requestId,ok:false,error:error.message});}
});
</script></body></html>'''


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("astrbot_source", type=Path)
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    settings = Config()
    settings.bind = [f"127.0.0.1:{args.port}"]
    asyncio.run(serve(build_app(args.astrbot_source), settings))

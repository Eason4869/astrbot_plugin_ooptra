"""Load the deployed Ooptra frontend; never fall back to a versioned snapshot."""

from __future__ import annotations

import asyncio
import base64
import re
from html import escape
from html.parser import HTMLParser

from .ooptra_client import OoptraError, console_resource_path

API_PATH = re.compile(r"/api/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\Z")


def adapt_script(source: str) -> tuple[str, bool]:
    """Keep upstream code and split scripts; replace only existing browser adapters."""
    start = source.find("async function api(path, opts = {}) {")
    found_api = start >= 0
    if found_api:
        end = source.find("\nfunction toast(", start)
        if end < 0:
            raise OoptraError("Ooptra 前端接口结构已变化，当前插件暂不兼容；请更新插件。")
        source = source[:start] + "async function api(path, opts = {}) { return window.OoptraPanel.api(path, opts); }\n" + source[end:]
    for old, new in (("localStorage.", "window.OoptraPanel.storage."),
                     ("sessionStorage.", "window.OoptraPanel.storage."),
                     ("new EventSource(", "new window.OoptraPanel.EventSource("),
                     ("window.open(", "window.OoptraPanel.openLink(")):
        source = source.replace(old, new)
    return source, found_api


class ConsoleDocument(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.html: list[str] = []
        self.assets: dict[str, str] = {}
        self.scripts: list[tuple[str | None, str]] = []
        self.script: tuple[str | None, list[str]] | None = None

    def asset(self, value: str) -> str:
        path = console_resource_path(value)
        if path not in self.assets:
            if len(self.assets) >= 24:
                raise OoptraError("控制台静态资源过多，暂不支持此页面。")
            self.assets[path] = f"__OOPTRA_RESOURCE_{len(self.assets)}__"
        return self.assets[path]

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script":
            if values.get("type", "text/javascript") not in {"text/javascript", "application/javascript"}:
                raise OoptraError("当前插件暂不支持此 Ooptra 脚本类型，请更新插件。")
            src = values.get("src")
            if src:
                self.asset(src)
            self.script = (console_resource_path(src) if src else None, [])
            return
        if tag == "base" or (tag == "meta" and values.get("http-equiv", "").lower() in {"refresh", "content-security-policy"}):
            raise OoptraError("控制台页面包含不支持的导航或安全策略。")
        adjusted = []
        for key, value in attrs:
            if value and ((key == "src" and tag in {"img", "source", "video", "audio"}) or (key == "href" and tag == "link")):
                if not value.startswith(("data:", "#")):
                    value = self.asset(value)
            adjusted.append(key if value is None else f'{key}="{escape(value, quote=True)}"')
        self.html.append("<" + tag + (" " + " ".join(adjusted) if adjusted else "") + ">")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            src, content = self.script
            self.scripts.append((src, "".join(content)))
            self.script = None
        else:
            self.html.append(f"</{tag}>")

    def handle_data(self, value):
        (self.script[1] if self.script is not None else self.html).append(value)

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")

    def handle_comment(self, value):
        if self.script is None:
            self.html.append(f"<!--{value}-->")

    def handle_decl(self, value):
        self.html.append(f"<!{value}>")


async def load_console(client) -> dict:
    raw, mime = await client.console_resource("/")
    if mime != "text/html":
        raise OoptraError("配置的地址未提供 Ooptra WebUI，请使用 WebUI 端口（默认 3090）。")
    document = ConsoleDocument()
    try:
        document.feed(raw.decode("utf-8-sig"))
        document.close()
        if document.script is not None or not document.scripts:
            raise OoptraError("部署中的控制台页面不完整，请检查 Ooptra。")
        paths = list(document.assets)
        resources = await asyncio.gather(*(client.console_resource(path) for path in paths))
        if len(raw) + sum(len(content) for content, _ in resources) > 12 * 1024 * 1024:
            raise OoptraError("控制台页面超过 12 MiB，暂不支持此部署。")
        fetched = dict(zip(paths, resources))
        html = "".join(document.html)
        for path, marker in document.assets.items():
            content, content_type = fetched[path]
            if content_type == "text/css":
                css = content.decode("utf-8-sig")
                urls = re.findall(r"url\(\s*['\"]?([^\s)'\"]+)", css, flags=re.I)
                if re.search(r"@import\b", css, flags=re.I) or any(not url.startswith(("#", "data:")) for url in urls):
                    raise OoptraError("部署中的控制台使用了尚不兼容的外部样式资源，请更新插件。")
            html = html.replace(marker, f"data:{content_type};base64,{base64.b64encode(content).decode('ascii')}")
        scripts, found_api = [], False
        for path, inline in document.scripts:
            source = fetched[path][0].decode("utf-8-sig") if path else inline
            adapted, present = adapt_script(source)
            scripts.append(adapted)
            found_api |= present
        if not found_api:
            raise OoptraError("Ooptra 前端接口结构已变化，当前插件暂不兼容；请更新插件。")
        return {"html": html, "scripts": scripts}
    except (UnicodeError, ValueError) as exc:
        raise OoptraError("无法解析部署中的 Ooptra 控制台页面。") from exc

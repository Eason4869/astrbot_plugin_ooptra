"""Bundle the MIT-licensed Ooptra console, with explicit AstrBot adaptations.

Usage: python tools/sync_ooptra_console.py /path/to/Ooptra
The original application's style and interaction code remain the UI authority.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def bundle(source: Path) -> None:
    assets = source / "src/webui/assets"
    target = Path(__file__).resolve().parents[1] / "pages/control"
    target.mkdir(parents=True, exist_ok=True)
    app = (assets / "app.js").read_text(encoding="utf-8")
    start = app.index("async function api(path, opts = {}) {")
    end = app.index("\nfunction toast(", start)
    app = app[:start] + "async function api(path, opts = {}) {\n  return window.OoptraPanel.api(path, opts);\n}\n" + app[end:]
    app = app.replace("localStorage.", "window.OoptraPanel.storage.")
    app = app.replace("sessionStorage.", "window.OoptraPanel.storage.")
    app = app.replace("new EventSource(", "new window.OoptraPanel.EventSource(")
    app = app.replace("window.open(", "window.OoptraPanel.openLink(")
    (target / "console-app.js").write_text("/* Bundled from Ooptra; see CONSOLE_SOURCE.md. */\n" + app.rstrip() + "\n", encoding="utf-8", newline="\n")
    maintenance = (assets / "maintenance.js").read_text(encoding="utf-8")
    original = "link.href = withToken('/api/maintenance/backups/' + encodeURIComponent(item.id));"
    replacement = "link.href = '#';\n      link.addEventListener('click', event => { event.preventDefault(); window.OoptraPanel.downloadBackup(item.id).catch(err => toast('下载失败', err.message, 'err')); });"
    if original not in maintenance:
        raise ValueError("Ooptra backup link changed; review the download adaptation.")
    maintenance = maintenance.replace(original, replacement)
    (target / "console-maintenance.js").write_text(maintenance.rstrip() + "\n", encoding="utf-8", newline="\n")
    for name, output in (("style.css", "console.css"), ("logo.svg", "logo.svg"), ("favicon.svg", "favicon.svg")):
        (target / output).write_bytes((assets / name).read_bytes())
    html = (assets / "index.html").read_text(encoding="utf-8")
    html = html.replace('<script src="/assets/theme.js"></script>', '')
    html = html.replace('href="/assets/style.css"', 'href="./console.css"')
    html = html.replace('src="/assets/', 'src="./').replace('href="/assets/', 'href="./')
    # AstrBot injects its SDK at the end of body. Deferred scripts run after it,
    # in document order and before the console's DOMContentLoaded boot handler.
    html = html.replace('<script src="./app.js"></script>', '<script defer src="./console-adapter.js"></script>\n<script defer src="./console-app.js"></script>')
    html = html.replace('<script src="./maintenance.js"></script>', '<script defer src="./console-maintenance.js"></script>')
    html = html.replace('<div class="page-tools" id="page-tools"></div>', '<a class="btn ghost" href="./index.html">返回插件工作台</a>\n        <div class="page-tools" id="page-tools"></div>')
    html = html.replace('</head>', '<link rel="stylesheet" href="./console-extra.css" />\n</head>')
    (target / "full.html").write_text(html, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    bundle(parser.parse_args().source)

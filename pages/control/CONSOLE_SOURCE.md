# Ooptra console source

The full console is bundled from [Eason4869/Ooptra](https://github.com/Eason4869/Ooptra),
version **3.0.2**, commit **8f3552f**, under the MIT license (Copyright 2026 Eason4869).
The plugin is distributed under the same license; see the repository's `LICENSE`.

`console.css`, `logo.svg`, and `favicon.svg` are copied unchanged from
`src/webui/assets/`. `full.html` and `console-app.js` retain the original layout and
interaction code with these explicit compatibility changes:

- Static asset URLs are relative to this AstrBot view.
- A return link opens the plugin's voice/group-binding workbench.
- The central HTTP helper calls `console-adapter.js`, which uses the AstrBot bridge.
- Browser LocalStorage and SessionStorage are replaced with per-page memory.
- Log events and downloads use the authenticated bridge instead of native
  EventSource or popups. External GitHub links show a selectable address.
- Theme controls follow AstrBot's theme and permit a temporary page toggle.

The root `logo.png` is a 512 x 512 transparent rasterization of the unchanged
Ooptra `logo.svg`, used because AstrBot discovers plugin logos as `logo.png`.
Regenerate it with `node tools/render_plugin_logo.cjs /path/to/node_modules/playwright`
using the system Microsoft Edge browser.

Regenerate the bundle after reviewing an Ooptra frontend update:

```bash
python tools/sync_ooptra_console.py /path/to/Ooptra
```

Keep the route allowlist in `webui.py` aligned with the new frontend, then run the
Python tests, JavaScript adapter tests, and browser smoke checks. The bundle is a
versioned snapshot; it does not automatically execute frontend code downloaded
from the configured Ooptra server. New or removed server APIs can require an
updated plugin bundle.

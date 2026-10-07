# Ooptra console source and runtime compatibility

Starting with **1.2.3**, the full console loads from the **deployed Ooptra instance
configured by api_base**, normally `http://127.0.0.1:3090`. HTML, stylesheet,
images and scripts are read again whenever the console opens or refreshes.
No Ooptra application snapshot is packaged or used as a fallback.
The frontend belongs to [Eason4869/Ooptra](https://github.com/Eason4869/Ooptra),
distributed under the MIT license (Copyright 2026 Eason4869).

`live_console.py` reads the deployed scripts in document order, including split
files such as `config.js` and `voice.js`. The `full.html` shell waits for AstrBot's
injected SDK. Its loader replaces the visible document while preserving the SDK
and message listeners, then executes the upstream scripts and initializes their
boot handlers.

Explicit sandbox adaptations:

- The central HTTP helper uses `console-adapter.js` and the authenticated bridge.
  Only reviewed HTTP methods and paths in `webui.py` are permitted.
- LocalStorage and SessionStorage become per-page memory.
- From 1.2.4, native `/api/auth/status` is translated using an actual protected
  Bearer probe because the native endpoint reports browser cookies only. Native
  login/setup validate the entered password before saving the plugin token.
  Logout blocks this page's API, log and download access while preserving the
  shared backend credential for plugin operations; reopening verifies it again.
- Log events and downloads use the bridge; backup IDs are validated and each
  buffered backup is limited to 128 MiB.
- Static URLs become data URLs; the browser needs no network access to Ooptra.
  External links display a selectable address.
- A return link opens the plugin's voice/group-binding workbench. Initial theme
  follows AstrBot; the deployed theme script owns its toggle.

The backend attaches the configured Ooptra token, accepts no destination URL
from the browser, follows no redirects and fetches no external frontend resource.
Each resource is limited to 2 MiB, at most 24 assets are read and the page/asset
total is limited to 12 MiB. Current plain JavaScript and flat `/assets/` resources
are supported. ES modules, CSS imports or dependent CSS resource URLs require
additional compatibility work. Unsupported documents, missing assets and script
startup errors display a failure with retry and return controls.

This removes frontend snapshot drift. Future changes to the API helper, resource
structure or API routes may still require a plugin update. Configure a trusted
Ooptra instance: its code executes inside the plugin sandbox and can use its
existing authenticated bridge. Deployment upgrades/restores are performed and
validated by Ooptra; automated browser checks use a mocked backend.
The 1.2.3 browser checks use the Ooptra 3.2.0 frontend at commit
`254cc95` and AstrBot's actual SDK/HTML processor; they cover refresh after a
deployment change, startup errors and offline retry. They do not certify a
production upgrade, real credentials or model audio.

The 1.2.4 checks use the updated 3.2.0 frontend at commit `9a8522d`, including
native session authentication. All 160 Python and 10 Node regression tests pass.
Browser checks use AstrBot's actual SDK/HTML processor and a mocked Ooptra API,
covering logout, incorrect password and login. A separate isolated HTTP check
uses Ooptra's unchanged native auth module and middleware, exercising wrong
credentials, UTF-8 Bearer passwords, first setup and logout independence. These
checks do not connect to the user's production deployment.

The quick workbench remains bundled. Its base stylesheet `workbench-base.css`,
unchanged `logo.svg` and `favicon.svg`
originate from Ooptra commit `4d3e4a526c13d4076fa989537be736747c897130`.
Root `logo.png` is a transparent 512 x 512 rasterization for AstrBot's plugin list.
Regenerate with `node tools/render_plugin_logo.cjs /path/to/node_modules/playwright`.

# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号尽量符合 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.4.0] - 2026-09-29

配套 **Ooptra ≥ 2.0.0**。本版修掉的都是「看起来正常、实际在骗你」的问题。

### Security

- **HTTP 请求改为 `trust_env=False`**：httpx 与 requests 不同，**不会**自动绕过 localhost 代理。此前系统里只要有 `HTTP_PROXY`，发往 `127.0.0.1` 的请求就会被送到代理——表现为「Ooptra 正常但报无法连接」，且 **Bearer token 与请求体会被完整交给代理**
- 404 错误信息不再混同「未启用 VOICE_API / 端口写错 / 版本过旧」三种原因，改为带端点名与版本要求

### Fixed

- **`/进语音` 不再跨域**：Ooptra 的 `default_channel` 属于**它的** `default_area`，群绑的是别的域时会直接拿它进房。现在域不一致就不再采用该默认频道，进房前还会到域内频道表核实一次，确认不属于本域则明确拒绝
- **非 dict 响应不再被伪造成成功**：`join()`/`leave()` 旧行为对非 dict 响应返回 `{"ok": True, "raw": …}`——进房其实失败了却报成功；现在抛 `OoptraError`
- **空响应体不再当成功**：旧实现 `if not resp.content: return {}`，代理返回 200 空体时会被当成「成功且无数据」
- **契约不符不再伪装成空数据**：`{"raw": …}` 没有任何 formatter 读，会显示成「当前没有人在语音频道」；现在明确提示结构不符合契约
- **`/语音自检` 不再误报正常**：`health()` 的回退条件从「除 401 外一律回退」收窄到 404/405/501，并标明数据来自哪个端点；服务真的挂掉时不再白等两个 timeout
- **持锁挂起**：进房失败分支的 `yield` 曾位于 `async with self._voice_op_lock` 内，生成器一挂起锁就被一直握着，期间并发 `/退语音` 全部阻塞
- **LLM 工具与斜杠命令选域不一致**：`_resolve_area_channel` 未传 `preferred_area`，多域群里 LLM 工具会选到第一个域，与 README 承诺的「按默认域择一」不符
- `_ID_SAFE_RE` 的 `$` 改为 `\Z`：`"chan-1\n"` 此前会被判成合法频道 ID

### Changed

- **`/进语音` `/退语音` 返回大幅精简**：不再回显整个响应体（`已请求进入语音：x（a / c）\n详情：{...}` → `已进语音：开黑房`）
- `/语音自检` 改为逐条探测契约端点（`/health`、`/voice/status`、`/voice/channels`、`/voice/members`），能区分「服务不可用」与「服务缺端点」
- LLM 进/退房工具返回同样精简

### Added

- `tools/mock_voice_api.py` 补上 `GET /voice/channels`，并把数据改成**两个域 + 多个频道**、`/voice/members` 按 area/channel 区分；跨域 join 会被 mock 拒绝（单域单频道发现不了这些问题）
- 新增 `tests/test_client_http.py`（`httpx.MockTransport`，覆盖 401/404/≥400/空体/非 JSON/`ok:false`/非 dict/超时/连接失败、`health()` 回退边界、`trust_env`）
- 新增 `tests/test_main_logic.py`（假 astrbot 模块加载 `main.py`，覆盖跨域保护、选域一致性、锁释放时机、返回文案、自检探测）
- 单测补契约告警与 ID 正则用例

## [0.3.0] - 2026-09-29

### Added

- `/语音状态` 改为按域汇总各语音频道在线人数（名称+人数），不再要求配置默认频道
- `/进语音` 支持按频道名或频道 ID 指定目标；不带参数时进默认频道
- 一群多域绑定：`group_map` 支持 `"areas": ["ID1","ID2"]`，查询与进房按 Ooptra 默认域择一
- 群未绑定频道时，回退到 Ooptra WebUI「设为默认」写入的默认频道
- 新增 VOICE_API 契约 `GET /voice/channels?area=`

### Changed

- `api_token` 说明与 Ooptra 侧对齐：默认填 `WEBUI_CONFIG.token`，独立 VOICE_API 才填 `VOICE_API_CONFIG.token`
- `/语音帮助` 文案同步新指令用法

## [0.2.1] - 2026-09-29

### Fixed

- 安装/加载失败 `ModuleNotFoundError: No module named 'ooptra_client'`：改为包内相对导入 `from .ooptra_client import ...`（AstrBot 以 `data.plugins.<name>.main` 包路径加载插件，同级模块不能按顶层绝对导入）

## [0.2.0] - 2026-09-29

### Security

- 进语音默认**仅允许本群绑定频道**；自定义频道需管理员或 `allow_arbitrary_channel`
- LLM `join_oopz_voice` 与指令共用同一套目标限制
- 错误信息截断，降低响应体泄漏

### Fixed

- `enable_llm_tools` 关闭后 LLM 语音工具会真正拒绝执行
- 移除无效的 `cmd_voice_*` 配置（原先只改帮助文案、不改真实指令）
- LLM 工具去掉假参数（`dummy` / `_unused`），无参工具不再误导模型
- `/语音绑定 域ID 备注` 误把中文备注写入频道 ID 的问题（自动识别为备注）
- `timeout_sec` 为 0 时被当成未配置的问题

### Changed

- 进/退语音增加约 3 秒冷却与并发锁
- `format_members` 在 `count` 与列表长度不一致时同时展示；兼容 `m`/`hm` 闭麦字段
- mock 增加线程锁，去掉无用 import，并注明不校验 token
- README 补充安全默认与字段说明

### Added

- 单测：ID/备注启发式、`count` 不一致、`m/hm` 字段
- 插件 `logo.png`（256×256），符合 AstrBot 插件市场展示要求
- `metadata.yaml` 补齐市场字段：`repo` / `short_desc` / `tags` / `social_link`，描述支持 Markdown

## [0.1.1] - 2026-09-29

### Changed

- `api_base` 默认值改为 `http://127.0.0.1:3090`
- 插件展示名改为 **Oopz 语音桥**
- 文档与 mock 默认端口同步为 `3090`

## [0.1.0] - 2026-09-29

### Added

- 首个可用版本
- QQ 指令：语音状态/进退语音/绑定解绑/自检/帮助
- LLM 工具与 VOICE_API 客户端、mock、基础单测

[0.4.0]: https://github.com/Eason4869/astrbot_plugin_ooptra/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Eason4869/astrbot_plugin_ooptra/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Eason4869/astrbot_plugin_ooptra/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Eason4869/astrbot_plugin_ooptra/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Eason4869/astrbot_plugin_ooptra/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Eason4869/astrbot_plugin_ooptra/releases/tag/v0.1.0

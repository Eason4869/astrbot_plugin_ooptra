# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号尽量符合 [语义化版本](https://semver.org/lang/zh-CN/)。

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

[0.3.0]: https://github.com/Eason4869/astrbot_plugin_ooptra/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Eason4869/astrbot_plugin_ooptra/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Eason4869/astrbot_plugin_ooptra/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Eason4869/astrbot_plugin_ooptra/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Eason4869/astrbot_plugin_ooptra/releases/tag/v0.1.0

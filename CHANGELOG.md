# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号尽量符合 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.1] - 2026-09-29

### Changed

- `api_base` 默认值改为 `http://127.0.0.1:3090`（与 Ooptra WebUI 同源，便于后续统一合并）
- 插件展示名改为 **Oopz 语音桥**（含 “Oopz” 标识）
- 文档与 mock 默认端口同步为 `3090`

## [0.1.0] - 2026-09-29

### Added

- 首个可用版本（AstrBot 插件 `astrbot_plugin_ooptra`）
- QQ 指令：
  - `语音状态` / `语音人数` — 查询绑定频道人数与成员状态
  - `进语音` / `退语音` — Bot 进入或离开 Oopz 语音频道
  - `语音绑定` / `语音解绑` —（管理员）维护 QQ 群 ↔ Oopz 域/频道映射
  - `语音自检` — 检测 Ooptra VOICE_API 连通性
  - `语音帮助` — 指令说明
- LLM 工具（Function Calling）：
  - `query_oopz_voice_members`
  - `query_oopz_voice_status`
  - `join_oopz_voice`
  - `leave_oopz_voice`
- WebUI 插件配置：`api_base`、`api_token`、`timeout_sec`、`group_map`、进房权限等
- `ooptra_client` 异步 HTTP 客户端（`httpx`），对接 Ooptra VOICE_API 契约
- `tools/mock_voice_api.py` 本地模拟 VOICE_API，便于在 Ooptra 能力就绪前联调
- 单元测试：群映射解析与成员/状态文案格式化

### Notes

- 本插件是 [Ooptra](https://github.com/Eason4869/Ooptra) 的**配套插件**，推荐与 Ooptra 配合使用
- 依赖 AstrBot `>= 4.16`，消息平台建议 aiocqhttp / QQ 官方机器人

[0.1.1]: https://github.com/Eason4869/astrbot_plugin_ooptra/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Eason4869/astrbot_plugin_ooptra/releases/tag/v0.1.0

# astrbot_plugin_ooptra

> **Ooptra 配套插件。** 本插件用于 [AstrBot](https://github.com/AstrBotDevs/AstrBot)，通过 HTTP 调用 [Ooptra](https://github.com/Eason4869/Ooptra) 的 `VOICE_API`，让你在 **QQ** 里查询 / 管理 **Oopz 语音频道**。
>
> **推荐与 [Ooptra](https://github.com/Eason4869/Ooptra) 配合使用**：Ooptra 负责 Oopz ↔ OneBot 文字桥接与语音进房，本插件负责 QQ 侧指令与群映射。单独使用本插件无法进语音（需要 Ooptra 提供 VOICE_API）。

**Oopz 语音桥 · QQ ↔ Oopz**

在 QQ 群里查语音人数、看状态，一键让 Bot 进/退 Oopz 语音频道。

---

## 功能一览

| 指令 | 说明 | 权限 |
|------|------|------|
| `/语音状态` `/语音人数` | 查看绑定频道的人数与成员（含闭麦/闭听） | 全员 |
| `/进语音 [频道ID]` | Bot 进入绑定或指定的 Oopz 语音频道 | 可配置 |
| `/退语音` | Bot 退出语音 | 可配置 |
| `/语音绑定 <域ID> [频道ID] [备注]` | 将当前 QQ 群绑定到 Oopz 域/频道 | 管理员 |
| `/语音解绑` | 解除当前群绑定 | 管理员 |
| `/语音自检` | 检测 Ooptra VOICE_API 连通性 | 全员 |
| `/语音帮助` | 指令说明 | 全员 |

同时向大模型注册工具（可选）：

- `query_oopz_voice_members` — 查语音人数
- `query_oopz_voice_status` — 查进房状态
- `join_oopz_voice` / `leave_oopz_voice` — 进/退语音

模型在对话里可自行调用，例如用户问「现在语音里有谁」。

---

## 架构位置

```text
QQ 客户端
   │  OneBot / 官方 API
   ▼
AstrBot  ──本插件──HTTP──►  Ooptra VOICE_API  ──►  Oopz / Agora 语音房
   ▲
   │  reverse WebSocket（OneBot v11）
Ooptra  ◄── Oopz 文字消息
```

- **文字**人格与记忆：留在 AstrBot
- **语音**进房 / 对话大脑：在 [Ooptra](https://github.com/Eason4869/Ooptra)（模式 B：本地大脑 + 可共享记忆）
- **本插件**：群映射、状态查询、进退房指令，不跑 LLM 对话

---

## 前置条件

1. 已安装并运行 **[Ooptra](https://github.com/Eason4869/Ooptra)**，且启用 `VOICE_API`（默认 `http://127.0.0.1:3090`）
2. **AstrBot ≥ 4.16**，已接入 QQ（aiocqhttp / QQ 官方机器人）
3. 本插件依赖 `httpx`

### Ooptra VOICE_API 契约

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查（可选） |
| GET | `/voice/status` | Bot 是否在语音房、域/频道/状态 |
| GET | `/voice/members?area=&channel=` | 频道在线人数与成员 |
| POST | `/voice/join` | Body: `{"area","channel"}` |
| POST | `/voice/leave` | 退出语音 |
| — | `Authorization: Bearer <token>` | token 为空则不校验 |

> 若 Ooptra 尚未实现 VOICE_API，可用仓库内 `tools/mock_voice_api.py` 本地模拟，便于先联调本插件。

---

## 安装

1. 将本仓库放入 AstrBot 插件目录，例如：

   ```text
   AstrBot/data/plugins/astrbot_plugin_ooptra/
   ```

2. 安装依赖（AstrBot 也会按 `requirements.txt` 自动安装）：

   ```bash
   pip install -r requirements.txt
   ```

3. 在 AstrBot **WebUI → 插件** 中启用 `astrbot_plugin_ooptra`，配置：
   - `api_base`：Ooptra VOICE_API 地址（默认 `http://127.0.0.1:3090`）
   - `api_token`：与 Ooptra `VOICE_API_CONFIG.token` 一致

4. 在目标 QQ 群发送：

   ```text
   /语音绑定 <Oopz域ID> <频道ID> 开黑房
   /语音自检
   /语音状态
   ```

---

## 配置项

| 配置 | 默认 | 说明 |
|------|------|------|
| `api_base` | `http://127.0.0.1:3090` | Ooptra VOICE_API 根地址，末尾不要 `/` |
| `api_token` | 空 | Bearer 令牌，与 Ooptra 侧一致 |
| `timeout_sec` | `8` | HTTP 超时（秒） |
| `group_map` | `{}` | QQ 群号 → Oopz 映射，也可用 `/语音绑定` 写入 |
| `allow_join` | `true` | 是否允许进/退语音指令 |
| `join_admin_only` | `false` | 进/退语音是否仅限管理员 |
| `enable_llm_tools` | `true` | 是否向 LLM 提供语音工具 |

### group_map 示例

```json
{
  "123456789": {
    "area": "6ad0261bc4eb41e882692531981a0972",
    "channel": "channel-uid-xxx",
    "label": "开黑房"
  }
}
```

也支持简写字符串：`"123456789": "域ID:频道ID"`。

---

## 本地开发

```text
astrbot_plugin_ooptra/
├── main.py                 # AstrBot 插件入口（指令 + LLM 工具）
├── ooptra_client.py        # VOICE_API 客户端与文案格式化
├── _conf_schema.json       # WebUI 配置表单
├── metadata.yaml           # 插件元数据
├── tools/mock_voice_api.py # 本地模拟 VOICE_API
└── tests/test_client_unit.py
```

跑单元测试：

```bash
python -m unittest discover -s tests -v
```

模拟 Ooptra API：

```bash
python tools/mock_voice_api.py 3090
```

在 AstrBot 中修改代码后，可在 WebUI 插件管理处 **重载插件** 热更新。

---

## 常见问题

**`无法访问 Ooptra VOICE_API`**

1. Ooptra 是否已启动，`VOICE_API` 是否启用  
2. `api_base` / `api_token` 是否与 Ooptra 一致  
3. 跨机部署时是否放行防火墙 / 端口  

**`群 xxx 未绑定 Oopz 频道`**

管理员在群内执行：`/语音绑定 <域ID> [频道ID] [备注]`

**进房后听不到说话 / 无人说话**

进房与推流由 [Ooptra](https://github.com/Eason4869/Ooptra) 负责；请检查 Ooptra 日志与 Web 控制台。本插件只触发进/退与查询。

---

## 相关项目

| 项目 | 说明 |
|------|------|
| **[Ooptra](https://github.com/Eason4869/Ooptra)** | Oopz → OneBot v11 反向 WebSocket 桥接 + Web 控制台（**本插件配套项目，推荐一起用**） |
| [Oopzbot](https://github.com/tangqingfeng7/Oopzbot) | 社区多功能 Oopz 频道机器人（Ooptra 派生来源） |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | 多平台 LLM 聊天机器人框架 |
| [OneBot v11](https://github.com/botuniverse/onebot-11) | 协议标准 |

---

## 使用边界

请只在自己的账号与有权管理的社群中使用，遵守 Oopz 用户协议与当地法律。本项目与 Oopz / 小米 / AstrBot 官方无隶属关系，按 MIT 许可提供，不作任何担保。

## License

[MIT](./LICENSE)

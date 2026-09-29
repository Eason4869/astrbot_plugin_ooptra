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
| `/语音状态` `/语音人数` | 查看绑定频道人数与成员（含闭麦/闭听） | 全员 |
| `/进语音 [频道ID]` | Bot 进入绑定频道；指定其他频道需管理员或开启自定义 | 可配置 |
| `/退语音` | Bot 退出语音 | 可配置 |
| `/语音绑定 <域ID> [频道ID] [备注]` | 将当前 QQ 群绑定到 Oopz 域/频道 | 管理员 |
| `/语音解绑` | 解除当前群绑定 | 管理员 |
| `/语音自检` | 检测 Ooptra VOICE_API 连通性 | 全员 |
| `/语音帮助` | 指令说明 | 全员 |

可选 LLM 工具（`enable_llm_tools`）：

- `query_oopz_voice_members` / `query_oopz_voice_status`
- `join_oopz_voice` / `leave_oopz_voice`

关闭后工具会返回「已禁用」。`join` 同样受绑定频道与权限约束。

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
- **语音**进房 / 对话大脑：在 [Ooptra](https://github.com/Eason4869/Ooptra)
- **本插件**：群映射、状态查询、进退房指令，不跑 LLM 对话

---

## 前置条件

1. 已运行 **[Ooptra](https://github.com/Eason4869/Ooptra)**，并启用 `VOICE_API`（默认与 WebUI 同源：`http://127.0.0.1:3090`）
2. **AstrBot ≥ 4.16**，已接入 QQ（aiocqhttp / QQ 官方机器人）
3. 依赖 `httpx`

### Ooptra VOICE_API 契约

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查（可选，失败会回退 `/voice/status`） |
| GET | `/voice/status` | Bot 是否在语音房 |
| GET | `/voice/members?area=&channel=` | 频道在线人数与成员 |
| POST | `/voice/join` | Body: `{"area","channel"}` |
| POST | `/voice/leave` | 退出语音 |
| — | `Authorization: Bearer <token>` | token 为空则不校验 |

成员字段兼容 `mic`/`speaker` 或 `m`/`hm`（0=闭，1=开）。

> 未实现 VOICE_API 时可用 `tools/mock_voice_api.py` 联调（**mock 不校验 token**）。

---

## 安装

1. 将本仓库放入 AstrBot 插件目录：

   ```text
   AstrBot/data/plugins/astrbot_plugin_ooptra/
   ```

2. 安装依赖：

   ```bash
   pip install -r requirements.txt
   ```

3. WebUI → 插件 → 启用 → 配置 `api_base` / `api_token`

4. 在目标 QQ 群：

   ```text
   /语音绑定 <Oopz域ID> <频道ID> 开黑房
   /语音自检
   /语音状态
   ```

---

## 配置项

| 配置 | 默认 | 说明 |
|------|------|------|
| `api_base` | `http://127.0.0.1:3090` | VOICE_API 根地址 |
| `api_token` | 空 | Bearer 令牌 |
| `timeout_sec` | `8` | HTTP 超时（秒），≤0 时回退 8 |
| `group_map` | `{}` | QQ 群号 → `{area, channel, label}` |
| `allow_join` | `true` | 是否允许进/退语音 |
| `join_admin_only` | `false` | 进/退语音是否仅管理员 |
| `allow_arbitrary_channel` | `false` | 是否允许进非绑定频道（管理员始终可临时指定） |
| `enable_llm_tools` | `true` | LLM 工具总开关（关闭后工具直接拒绝） |

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

也支持简写：`"123456789": "域ID:频道ID"`。

绑定时若把中文备注误填到频道位，会自动识别为备注（`/语音绑定 域ID 开黑房`）。

---

## 安全默认

- 进房默认**只允许本群绑定的频道**，减少误入/滥用
- 进/退语音有约 3 秒冷却，降低刷指令
- `api_token` 在 WebUI 中掩码显示
- 绑定 / 解绑需管理员

---

## 本地开发

```text
astrbot_plugin_ooptra/
├── main.py                 # 指令 + LLM 工具
├── ooptra_client.py        # VOICE_API 客户端与格式化
├── _conf_schema.json
├── metadata.yaml
├── tools/mock_voice_api.py
└── tests/test_client_unit.py
```

```bash
python -m unittest discover -s tests -v
python tools/mock_voice_api.py 3090
```

改代码后可在 AstrBot WebUI 重载插件。

---

## 常见问题

**无法访问 Ooptra VOICE_API**  
查 Ooptra 是否启动、`api_base`/`api_token` 是否一致、防火墙。

**群 xxx 未绑定 Oopz 频道**  
管理员：`/语音绑定 <域ID> [频道ID] [备注]`

**提示仅允许进入绑定频道**  
更新绑定，或由管理员 `/进语音 <频道ID>`，或打开 `allow_arbitrary_channel`。

**进房后听不到说话**  
进房与推流由 [Ooptra](https://github.com/Eason4869/Ooptra) 负责，见其日志/控制台。

---

## 相关项目

| 项目 | 说明 |
|------|------|
| **[Ooptra](https://github.com/Eason4869/Ooptra)** | 配套项目，推荐一起用 |
| [Oopzbot](https://github.com/tangqingfeng7/Oopzbot) | 社区 Oopz 机器人 |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | 多平台机器人框架 |
| [OneBot v11](https://github.com/botuniverse/onebot-11) | 协议 |

---

## 使用边界

请只在自己的账号与有权管理的社群中使用。与 Oopz / 小米 / AstrBot 官方无隶属关系，MIT 许可，不作担保。

## License

[MIT](./LICENSE)

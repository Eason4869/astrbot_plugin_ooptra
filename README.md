# astrbot_plugin_ooptra

> **Ooptra 配套插件。** 本插件用于 [AstrBot](https://github.com/AstrBotDevs/AstrBot)，通过 HTTP 调用 [Ooptra](https://github.com/Eason4869/Ooptra) 的 `VOICE_API`，让你在 **QQ** 里查询 / 管理 **Oopz 语音频道**。
>
> **推荐与 [Ooptra](https://github.com/Eason4869/Ooptra) 配合使用**：Ooptra 负责 Oopz ↔ OneBot 文字桥接与语音进房，本插件负责 QQ 侧指令与群映射。单独使用本插件无法进语音（需要 Ooptra 提供 VOICE_API）。
>
> **需要 Ooptra ≥ 2.0.0**（`GET /voice/channels` 从 2.0.0 起才有）。用 `/语音自检` 可以逐条确认。

**Oopz 语音桥 · QQ ↔ Oopz**

在 QQ 群里查语音人数、看状态，一键让 Bot 进/退 Oopz 语音频道。

---

## 功能一览

| 指令 | 说明 | 权限 |
|------|------|------|
| `/语音状态` `/语音人数` | 查看绑定域内各语音频道在线人数（无需默认频道） | 全员 |
| `/进语音 [频道名\|频道ID]` | 进默认频道；也可按频道名/ID 指定其他频道 | 可配置 |
| `/退语音` | Bot 退出语音 | 可配置 |
| `/语音方案` | 查询当前语音方案（无需群绑定） | 全员 |
| `/语音方案 gemini` / `/语音方案 mimo` | 切换 Gemini Live / MiMo 级联 | 默认仅管理员 |
| `/语音绑定 <域ID> [频道ID] [备注]` | 将当前 QQ 群绑定到 Oopz 域/频道 | 管理员 |
| `/语音解绑` | 解除当前群绑定 | 管理员 |
| `/语音自检` | 检测连通性并逐条探测契约端点 | 全员 |
| `/语音帮助` | 指令说明 | 全员 |

**默认目标**：在 Ooptra 控制台「语音台 → 会话控制」选中域/频道后点**「设为默认」**，即写入 `OOPZ_CONFIG.default_area` / `default_channel`。
- `/进语音` 不带参数时进**默认频道**（群绑定里没填频道时也用它）
- 一个 QQ 群绑了多个域（`group_map` 里 `areas` 为列表）时，状态查询、进房与 LLM 工具都按**默认域**择一
- **跨域保护**：Ooptra 的默认频道属于它的默认**域**。若本群绑的是别的域，该默认频道不会被采用（进房前还会到域内频道表核实一次），避免误入其他域的房间

可选 LLM 工具（`enable_llm_tools`）：

- `query_oopz_voice_members` / `query_oopz_voice_status`
- `join_oopz_voice` / `leave_oopz_voice`
- `set_oopz_voice_backend`：查询/切换语音方案（参数为空时仅查询，切换默认仅管理员）

关闭后工具会返回「已禁用」。`join` 同样受绑定频道与权限约束；方案切换工具与命令共用权限、锁与冷却。

### 切换语音方案

```text
/语音方案                 # 查询当前方案，全员可用
/语音方案 gemini          # 管理员切换至 Gemini Live
/语音方案 mimo            # 管理员切换至 MiMo 级联
```

也支持 `Gemini Live`、`MiMo 级联`、`gemini_live`、`mimo_cascade`；命令别名为 `/voice_backend`、`/切换语音方案`、`/语音切换`。启用 LLM 工具后，管理员也可通过自然语言要求切换。

切换复用 Ooptra WebUI 的 `POST /api/config`，只写入 `voice.backend`，由 Ooptra 持久化并热重载。请提前在 Ooptra 中配置相应方案的密钥与模型；切换沿用既有配置，不会自动开启语音对话。支持热重载时，已在房的 Bot 由 Ooptra 重建语音后端/会话；旧服务提示需要重启时，插件会显示提示，不会误报已生效。

**该选择是 Ooptra 实例的全局设置**，会影响连接同一实例的所有 QQ 群，无需先绑定群。`backend_admin_only` 默认 `true`（管理员判断与现有进退房工具一致，使用 AstrBot 的 `event.is_admin()`）；命令和 LLM 工具都受此限制，且与进/退房共用约 3 秒冷却和并发锁。

**切换必须使用 WebUI 地址**：`api_base` 默认 `http://127.0.0.1:3090`，`api_token` 填 `WEBUI_CONFIG.token`。独立 VOICE_API 端口提供查询/进退房接口，但不提供 `/api/config`；本功能只需更新插件，无需修改 Ooptra 代码。若服务器缺少该接口、鉴权失败、配置未确认保存或会话重建失败，插件会明确提示。

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

1. 已运行 **[Ooptra](https://github.com/Eason4869/Ooptra) ≥ 2.0.0**，并启用 `VOICE_API`（默认与 WebUI 同源：`http://127.0.0.1:3090`）
2. **AstrBot ≥ 4.16**，已接入 QQ（aiocqhttp / QQ 官方机器人）
3. 依赖 `httpx`

### Ooptra VOICE_API 契约

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查；**仅当服务不认识该端点（404/405/501）时**才回退 `/voice/status` |
| GET | `/voice/status` | Bot 是否在语音房（含默认域/默认频道） |
| GET | `/voice/channels?area=` | 域内语音频道与在线人数（**需 Ooptra ≥ 2.0.0**） |
| GET | `/voice/members?area=&channel=` | 频道在线人数与成员（`channel` 留空表示整域汇总） |
| POST | `/voice/join` | Body: `{"area","channel"}` |
| POST | `/voice/leave` | 退出语音 |
| POST | `/api/config` | Body: `{"updates":{"voice":{"backend":"gemini_live"}}}`（或 `mimo_cascade`）；仅 WebUI 提供，保存并应用后端选择 |
| — | `Authorization: Bearer <token>` | 与 Ooptra 侧令牌一致；两边都留空则不校验 |

成员字段兼容 `mic`/`speaker` 或 `m`/`hm`（0=闭，1=开）；Oopz 不返回静音状态时字段为 `null`（未知，不会伪造成「开麦」）。

**端口选哪个**：推荐 **`3090`**（Ooptra WebUI 端口，已含全部 VOICE_API 和方案切换需要的 `/api/config`）。Ooptra 2.0.0 起，可选的独立端口 `3091`（`VOICE_API_CONFIG`）与 `3090` 的 **VOICE_API 路由一致**，查询/进退房可使用任一端口；切换方案需用 WebUI 端口。

**令牌对齐**：走 `3090` 时鉴权用 **`WEBUI_CONFIG.token`**；只有单独开了 `VOICE_API_CONFIG`（`3091`）时才用 **`VOICE_API_CONFIG.token`**。本插件的 `api_token` 填其中生效的那个，两边都留空则不校验。

> 本插件的 HTTP 请求固定 `trust_env=False`：httpx 与 requests 不同，**不会**自动绕过 localhost 代理。否则系统里只要有 `HTTP_PROXY`，发往 `127.0.0.1` 的请求就会被送去代理（报「无法连接」且令牌外泄）。

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

3. WebUI → 插件 → 启用 → 配置 `api_base` / `api_token`（令牌与 Ooptra 侧生效项一致，默认填 `WEBUI_CONFIG.token`）

4. 在 Ooptra 控制台「语音台 → 会话控制」查看并复制**域 ID / 频道 ID**，然后在目标 QQ 群：

   ```text
   /语音绑定 <Oopz域ID> <频道ID> 开黑房
   /语音自检
   /语音状态
   ```

---

## 配置项

| 配置 | 默认 | 说明 |
|------|------|------|
| `api_base` | `http://127.0.0.1:3090` | VOICE_API 根地址（推荐 3090，= Ooptra WebUI 端口，已含全部 VOICE_API） |
| `api_token` | 空 | Bearer 令牌；填 Ooptra 的 `WEBUI_CONFIG.token`（用 3090 时），或独立 VOICE_API 时的 `VOICE_API_CONFIG.token` |
| `timeout_sec` | `8` | HTTP 超时（秒），≤0 时回退 8 |
| `group_map` | `{}` | QQ 群号 → `{area, channel, label}` |
| `allow_join` | `true` | 是否允许进/退语音 |
| `join_admin_only` | `false` | 进/退语音是否仅管理员 |
| `backend_admin_only` | `true` | 语音方案切换是否仅管理员；查询仍对全员开放，命令和 LLM 工具共用限制 |
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

一群绑多个域（查询/进房按 Ooptra 默认域择一）：

```json
{
  "123456789": {
    "areas": ["域ID-1", "域ID-2"],
    "channel": "channel-uid-xxx",
    "label": "多域群"
  }
}
```

绑定时若把中文备注误填到频道位，会自动识别为备注（`/语音绑定 域ID 开黑房`）。

---

## 安全默认

- 进房默认**只允许本群绑定的频道**，减少误入/滥用
- **跨域保护**：Ooptra 的默认频道不会把 Bot 带进本群绑定域之外的房间
- 请求固定 `trust_env=False`，令牌不会因为系统代理而外泄给第三方
- 进/退语音与方案切换共用并发锁和约 3 秒冷却，降低刷指令
- 方案切换默认仅管理员，影响 Ooptra 全局后端选择
- `api_token` 在 WebUI 中掩码显示
- 绑定 / 解绑需管理员

---

## 本地开发

```text
astrbot_plugin_ooptra/
├── main.py                    # 指令 + LLM 工具
├── ooptra_client.py           # VOICE_API 客户端与格式化
├── _conf_schema.json
├── metadata.yaml
├── tools/mock_voice_api.py    # 本地 mock（两域多频道、方案切换）
└── tests/
    ├── test_client_unit.py    # 纯函数
    ├── test_client_http.py    # HTTP 分支（httpx.MockTransport）
    ├── test_main_logic.py     # 指令层（跨域保护/锁/文案/自检）
    └── test_voice_backend.py  # 方案切换（权限/HTTP/并发/mock 联调）
```

```bash
python -m unittest discover -s tests -v     # 或 python -m pytest tests -q
python tools/mock_voice_api.py 3099         # 换个端口，避免和真实 Ooptra 撞
```

改代码后可在 AstrBot WebUI 重载插件。

---

## 常见问题

**无法访问 Ooptra VOICE_API**  
先跑 `/语音自检`。查 Ooptra 是否启动、`api_base`/`api_token` 是否一致、防火墙，以及**系统代理**（本插件已固定 `trust_env=False`，不受代理影响，但防火墙/端口仍可能拦）。

**`/语音自检` 里 `❌ GET /voice/channels`**  
Ooptra 版本低于 2.0.0，或 `api_base` 指向了别的服务。升级 Ooptra 到 2.0.0+ 即可。

**`/进语音` 提示「默认频道不属于本群绑定的域」**  
本群绑的域和 Ooptra 里「设为默认」的域不是同一个（多域群常见）。用 `/进语音 <频道名>` 指定，或 `/语音绑定 <域ID> <频道ID>` 绑到本域的频道。

**鉴权失败 / 401**  
`api_token` 要和 Ooptra 侧生效的令牌一致：默认是 Web 控制台的 `WEBUI_CONFIG.token`；若单独启用了 `VOICE_API_CONFIG`（独立端口），则是 `VOICE_API_CONFIG.token`。两边都留空表示不校验。

**群 xxx 未绑定 Oopz 频道**  
管理员：`/语音绑定 <域ID> [频道ID] [备注]`。域 ID / 频道 ID 可在 Ooptra 控制台「语音台 → 会话控制」页查看并一键复制。

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

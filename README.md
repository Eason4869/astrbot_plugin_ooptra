# astrbot_plugin_ooptra

> **Ooptra 配套插件。** 本插件用于 [AstrBot](https://github.com/AstrBotDevs/AstrBot)，通过 HTTP 调用 [Ooptra](https://github.com/Eason4869/Ooptra) 的 API，在 **AstrBot 工作台和 QQ** 中查询 / 管理 **Oopz 语音频道**。
>
> **推荐与 [Ooptra](https://github.com/Eason4869/Ooptra) 配合使用**：Ooptra 负责 Oopz ↔ OneBot 文字桥接与语音进房，本插件负责 QQ 侧指令与群映射。单独使用本插件无法进语音（需要 Ooptra 提供 VOICE_API）。
>
> **须使用 Ooptra 3.0 及以上版本（≥ 3.0.0）**，语音串门依赖 3.0 新增的分域自动串门接口。用 `/语音自检` 可以检查连接。

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
| `/语音串门 开\|关` | 开启/关闭本群绑定域的自动语音串门 | 仅管理员（不可配置放宽） |
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

### 自动语音串门

```text
/语音串门 开              # 管理员开启本群绑定域的自动串门
/语音串门 关              # 管理员关闭本群绑定域的自动串门
```

**仅 AstrBot 管理员可用**，使用 `event.is_admin()` 判断；不受 `join_admin_only`、`backend_admin_only` 或 LLM 工具开关影响，也没有放宽权限的配置。请先在 QQ 群用 `/语音绑定 <域ID> [频道ID] [备注]` 绑定域；私聊或未绑定群不能修改串门开关。一个群绑定多个域时，沿用状态查询的选择规则：优先选择 Ooptra 默认域（须在绑定列表内），否则选择首个绑定域。

命令调用 `POST /voice/auto-visit/config`，仅提交 `{"updates":{"areas":{"目标域ID":{"enabled":true}}}}`（关闭时为 `false`）。Ooptra 保存并热应用该域开关，其他域、全局规则、每日上限和域覆盖保持原有配置；连接同一 Ooptra 实例、绑定同一域的群共用此开关。命令与其他语音控制共用约 3 秒冷却和并发锁。

开启后按照 Ooptra 配置的概率、检查间隔、冷却和每日上限运行，不会立刻进房。请先配置语音后端并开启 Ooptra 语音总开关；域开关不会自动启用语音总开关或恢复已暂停的串门控制器。若控制器暂停，插件会提示在 Ooptra「语音台 → 自动串门」检查后恢复。缺少接口、鉴权失败、保存/应用失败或返回状态不符时会明确报错。

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

1. 已运行 **[Ooptra](https://github.com/Eason4869/Ooptra) 3.0 及以上版本（≥ 3.0.0）**，并启用 `VOICE_API`（默认与 WebUI 同源：`http://127.0.0.1:3090`）
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
| POST | `/voice/auto-visit/config` | Body: `{"updates":{"areas":{"域ID":{"enabled":true}}}}`；保存并应用分域串门开关（需 Ooptra ≥ 3.0.0） |
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

## AstrBot 内的 Ooptra 工作台

重载插件后，在 AstrBot WebUI 的插件详情中打开 **Ooptra 工作台**。需要支持
Plugin Pages / Views 的 AstrBot 后端和 Dashboard；旧版若没有页面入口，请一起
升级后端与 Dashboard。原有 QQ 命令与 LLM 工具仍可使用。

- **语音台**：当前房间和方案、域内频道人数、成员闭麦 / 闭听状态；进房、退房、
  Gemini Live / MiMo 方案切换，以及当前域的自动串门开关。
- **群绑定**：填写 QQ 群号、域 ID、可选频道 ID 和备注；可添加、编辑、移除。
  一个群的多个域可以逐行填写，保存时完整保留。编辑不会删除其他群或已有扩展字段。
- **完整控制台**：点击「打开完整控制台」，直接在 AstrBot 内进入 Ooptra 原界面，
  继续管理账号、模型配置、日志、人格、记忆和详细串门规则；点击「返回插件工作台」返回。

两种界面沿用 Ooptra 3.0.2 的视觉样式，并跟随 AstrBot 明暗主题。完整控制台使用
随插件打包的 Ooptra 3.0.2 前端和兼容层，数据及操作仍来自实际 Ooptra 服务。
Ooptra 更新增加新界面功能时，需要同步更新插件的前端快照，详见
[前端来源与同步方式](pages/control/CONSOLE_SOURCE.md)。

插件列表与工作台统一使用 Ooptra 蓝绿双对话环 LOGO，原紫色耳机标识已弃用。

**旧版完整控制台提示「请从 AstrBot 插件详情打开控制台」时**：更新插件到
**1.2.1 或更新版本**，重载插件并关闭、重新打开工作台。此修复适配 AstrBot
在页面底部注入桥接脚本的加载顺序，无需修改 Ooptra 的端口或令牌。

简易工作台顶部显示「**本插件需安装Ooptra方能完美运行**」，附
[Ooptra GitHub 仓库](https://github.com/Eason4869/Ooptra) 地址。
在 AstrBot 沙箱内点击该链接会显示地址及复制按钮，便于打开安装说明。

### 连接与权限

页面通过 **浏览器 → AstrBot → 插件 → Ooptra** 调用接口，使用已有 `api_base`
和 `api_token`，不会把 Ooptra 访问令牌下发到浏览器，也无需浏览器直接连接 Ooptra。
请让 `api_base` 指向 **WebUI 端口（默认 3090）**，独立 VOICE_API 端口不能提供完整控制台。
Docker 部署时，这个地址必须从 **AstrBot 容器内**可访问：两个容器可使用服务名，
访问宿主机可使用部署环境支持的宿主机地址；容器内 `127.0.0.1` 指向该容器自身。

页面仅供已登录 AstrBot 管理面板的用户使用，作为管理员操作入口。
进退房仍遵守 `allow_join`，而 `join_admin_only` / `backend_admin_only` 是对 QQ 命令
和 LLM 工具的限制。页面、QQ 命令和工具共用语音操作锁及约 3 秒冷却。
全局方案切换影响连接同一实例的所有群；分域串门影响绑定同一个域的群。
账号、人格等配置和保存后重新连接不会被语音冷却拦截。
停用或卸载插件会注销页面接口并关闭日志订阅，旧页面需启用插件后重新打开。

Ooptra 暂时离线时仍可管理本地群绑定。完整控制台在沙箱内使用页面内存保存临时
选项，模型配置与群绑定则分别持久化到 Ooptra 和插件配置。日志跟随与下载通过
AstrBot 桥接；完整控制台中的外部 GitHub 链接显示可复制地址。

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
├── main.py                    # 指令 + LLM 工具 + 页面生命周期
├── ooptra_client.py           # Ooptra 客户端与格式化
├── webui.py                   # 页面服务、群绑定与控制台路由白名单
├── web_routes.py              # AstrBot 原生 API / 旧版 Quart 适配
├── pages/control/             # 简易工作台、完整控制台与兼容层
├── _conf_schema.json
├── metadata.yaml
├── tools/mock_voice_api.py    # 本地 mock（两域多频道、方案切换）
└── tests/
    ├── test_client_unit.py    # 纯函数
    ├── test_client_http.py    # HTTP 分支（httpx.MockTransport）
    ├── test_main_logic.py     # 指令层（跨域保护/锁/文案/自检）
    ├── test_voice_backend.py  # 方案切换（权限/HTTP/并发/mock 联调）
    ├── test_webui.py          # 群绑定、完整控制台、冷却与停用保护
    ├── test_web_routes.py     # 可选 Quart 路由与 SSE 生命周期
    └── test_console_adapter.cjs # Node 内置测试运行器验证兼容层
```

```bash
python -m unittest discover -s tests -v     # 或 python -m pytest tests -q
python tools/mock_voice_api.py 3099         # 换个端口，避免和真实 Ooptra 撞
```

改代码后可在 AstrBot WebUI 重载插件。

页面兼容层测试无需 npm 依赖：`node --test tests/test_console_adapter.cjs`。
Quart 路由测试需要开发依赖 `quart`；生产环境不需要额外安装 Quart。

完整沙箱联调使用真实 AstrBot API 与官方桥接 SDK、模拟 Ooptra 数据，不会修改真实服务：

```bash
pip install fastapi hypercorn quart
python tools/webui_preview.py /path/to/AstrBot --port 18765
# 在另一个终端，使用已安装的 Playwright 包和系统 Microsoft Edge：
node tools/webui_smoke.cjs http://127.0.0.1:18765 /path/to/node_modules/playwright
```

AstrBot 源码需包含 `astrbot/api/web.py`、`astrbot/core/utils/upload.py`、
`astrbot/dashboard/plugin_page_bridge.js` 和 `astrbot/dashboard/services/plugin_page_service.py`。
测试复用实际 HTML 桥接注入逻辑与原生页面的 iframe 沙箱限制，
覆盖群绑定落盘、离线编辑、语音控制、保存并重连、日志、主题及手机布局；
证据输出到已忽略的 `.webui-evidence/`。真实部署仍需以自己的 AstrBot / Ooptra 连接验证。

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

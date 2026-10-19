# 人格与双向记忆开发说明

配套版本：astrbot_plugin_ooptra 2.0.1 / Ooptra 261019-dev（预设正式版 4.0.0）。

## 适配器边界

- session_sources.py：完整 UMO 校验与域默认／频道覆盖来源规则。
- context_sync.py：实际房间匹配来源、逐会话人格解析、多目标查询与写回、独立长轮询任务。
- voice_memory.py：限定已支持的记忆插件，保留其白名单、授权和采集范围。
- sync_state.py：SQLite 来源线索、稳定客户端标识、处理结果和逐角色回执。
- ooptra_client.py：复用现有 HTTP 客户端、Bearer 认证与 trust_env=False。

Ooptra 的「会话对应」页面通过 bindings 请求管理本插件的 group_map 与 context_sources，不维护第二份配置。
后台轮询在同步关闭时仍提供绑定管理；人格与记忆操作继续拒绝执行。WebUI 保存校验频道
属于所选域，允许多个会话共享范围，并在配置锁内核对来源仍已映射。命令使用事件完整 UMO，保留扩展字段。

不把语音转写投进普通 QQ 消息流水线，因此不会触发 QQ 命令、重复回复或旁路唤醒。
来源事件仅供记忆插件使用，禁止通过该事件向 QQ 发消息。

当前按上游源码接口核对：

- AstrBot e5e21b71：人格解析、会话管理和平台事件结构。
- Memory Companion e40a8300（2.3.1）：Bridge compose_context / remember / record_visible_turn。
- LivingMemory 694f04b0：ConversationManager、MemoryEngine、MemoryProcessor、MemoryReflection。

这不是对任意第三方版本的兼容承诺；运行时按接口探测，接口缺失时显示不兼容。
LivingMemory 存储需要真实消息回执，反思使用隔离的管理器视图避免重复插入已确认的助手记录，
继续使用原插件任务跟踪、历史限制和总结策略。主动过滤的错误回复与暂时失败分别处理。
Memory Companion 开启额外授权门时，未获授权的语音接入不会绕过它。

## 作用域与错误

人格来源单选，记忆来源最多 8 个，均使用完整 UMO。规则包含 persona_umo、persona_id、memory_umos、allow_private；查询全部所选目标并写回全部，保持各自的人格过滤。域默认与频道覆盖分开；显式空规则不回退到旧控制映射。

仅管理员确认的私聊可共享到公共语音房；私聊事件使用 FriendMessage 和目标用户作用域，保留实际 Oopz 发言者元信息。群检索不传私聊身份；QQ 号关联只用于标记真实 QQ 来源，不根据模型参数或昵称推断。目录选择会核对平台实例及 Ooptra 的 OneBot self_id。

读取与长时采集分开处理。普通采集返回 captured，明确长期写入返回 stored。
普通采集与明确记住请求的临时失败均保留在 Ooptra 待写队列，保持原始请求 ID。每个目标使用轮次 ID + UMO 摘要构成稳定 ID，逐目标／逐角色记录回执，重试跳过成功目标。queued=true 表示正在后台补写，模型不应再发一个新请求重复保存。来源列表、会话人格或实例身份变化会拒绝旧轮次，不能改写到新会话；部分成功不会报告全部已保存。
回执提供重试保护，但第三方落盘与本地回执之间不具有跨库原子事务，崩溃窗口需如实看待。
取消已进入第三方执行阶段的写入不等于撤销；停用插件会取消后台连接和未完成任务。

## 验证

```text
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -q
node --test tests/test_console_adapter.cjs
node --check pages/control/app.js
node --check pages/control/session-manager.js
node --check pages/control/console-adapter.js
node --check pages/control/console-loader.js
```

GitHub Actions 工作流见 [.github/workflows/ci.yml](../.github/workflows/ci.yml)：
Python 测试覆盖 Ubuntu 3.10／3.13 与 Windows 3.13，Node 测试使用 Node 22。
Python 任务安装 requirements-dev.txt，其中 Quart 用于完整执行 Web 路由测试，
避免可选依赖缺失时跳过这部分测试；它不增加插件运行依赖。测试通过模拟 AstrBot 和
第三方记忆插件接口运行，无需真实账号、API Token 或 AstrBot 服务。
Node 任务使用内置测试运行器，无需 npm 安装。

新增测试覆盖多映射与旧唯一绑定兼容、完整 UMO／私聊确认、人格单选／记忆多目标读写、部分失败重试、主插件选择、白名单、授权门、两种适配器的
读写调用、去重和停止行为。浏览器检查使用本地预览环境；真实语音识别质量、
模型是否按需主动检索以及真实群内体验仍需部署后验收。

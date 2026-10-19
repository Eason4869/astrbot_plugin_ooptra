# 人格与双向记忆开发说明

配套版本：astrbot_plugin_ooptra 2.0.0 / Ooptra 261019-dev（预设正式版 4.0.0）。

## 适配器边界

- context_sync.py：实际房间反查 group_map、平台和人格解析、独立长轮询任务。
- voice_memory.py：限定已支持的记忆插件，保留其白名单、授权和采集范围。
- sync_state.py：SQLite 来源线索、稳定客户端标识、处理结果和逐角色回执。
- ooptra_client.py：复用现有 HTTP 客户端、Bearer 认证与 trust_env=False。

Ooptra 的「QQ 群对应」页面通过 bindings 请求直接管理此插件的 group_map，不维护第二份配置。
后台轮询在同步关闭时仍提供绑定管理；人格与记忆操作继续拒绝执行。WebUI 保存校验频道
属于所选域，并在配置锁内拒绝重叠绑定。命令更新保留原有人格、平台及扩展字段。

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

来源必须唯一，包含 QQ 平台实例、群、人格过滤和记忆插件。成员 UID 不从模型参数或昵称
推断 QQ 号；仅接受管理员配置的显式关联。公共检索使用严格群作用域，不传私聊用户身份。

读取与长时采集分开处理。普通采集返回 captured，明确长期写入返回 stored。
临时失败保留在 Ooptra 待写队列；来源改变不将旧事件写入新群。
回执提供重试保护，但第三方落盘与本地回执之间不具有跨库原子事务，崩溃窗口需如实看待。
取消已进入第三方执行阶段的写入不等于撤销；停用插件会取消后台连接和未完成任务。

## 验证

```text
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -q
node --test tests/test_console_adapter.cjs
node --check pages/control/app.js
node --check pages/control/console-adapter.js
node --check pages/control/console-loader.js
```

GitHub Actions 工作流见 [.github/workflows/ci.yml](../.github/workflows/ci.yml)：
Python 测试覆盖 Ubuntu 3.10／3.13 与 Windows 3.13，Node 测试使用 Node 22。
Python 任务安装 requirements-dev.txt，其中 Quart 用于完整执行 Web 路由测试，
避免可选依赖缺失时跳过这部分测试；它不增加插件运行依赖。测试通过模拟 AstrBot 和
第三方记忆插件接口运行，无需真实账号、API Token 或 AstrBot 服务。
Node 任务使用内置测试运行器，无需 npm 安装。

新增测试覆盖绑定唯一性、人格解析、主插件选择、白名单、授权门、两种适配器的
读写调用、去重和停止行为。浏览器检查使用本地预览环境；真实语音识别质量、
模型是否按需主动检索以及真实群内体验仍需部署后验收。

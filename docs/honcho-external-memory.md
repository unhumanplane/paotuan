# Honcho 外置记忆接入指南

本文只说明 paotuan 的 Honcho 外置记忆接入：它能带来什么、如何部署、如何配置、如何使用，以及如何排错。

## 适用范围

Honcho 是可选增强层，不替代本地 `GameSession` 和 `JsonGameRepository`。本地 JSON 仍然是 HP、物品、位置、回合、规则结果、骰子结果等强一致事实的权威来源。

Honcho 适合保存和检索这些内容：

- 玩家偏好，例如叙事节奏、细节程度、战斗复杂度接受度、谜题偏好、recap 风格。
- 角色倾向，例如角色习惯、承诺、恐惧、伤痕、个人弧光。
- 关系和长期互动，例如和 NPC、阵营、地点的历史关系。
- 章节 recap、幕间总结、未解决伏笔和关键事件。
- DM 风格反馈，例如玩家更喜欢快节奏、谨慎裁定或更多环境描写。

Honcho 不适合作为这些内容的权威来源：

- HP、AC/DC、伤害、豁免、骰子结果。
- 物品、装备、资源、法术位、坐标、位置、回合顺序。
- 规则执行结果、战棋合法性、工具返回的确定性事实。
- 原始聊天流、完整 prompt、原始 audit、凭证、服务器路径或本地环境隐私。

## 接入后能带来什么

启用 Honcho 后，paotuan 会获得几类长期记忆能力。

1. Prompt 前辅助回忆

   每轮回复前，router 会读取 Honcho context，并把它作为单独的“外部 Honcho 辅助记忆”段落注入 prompt。这个段落只作辅助回忆，不能覆盖本地状态和工具结果。

2. 关键事件和摘要同步

   本地完成叙事轨迹或 memory compression 后，插件会把受控摘要和关键事件写入 Honcho。写入内容是摘要，不是完整原始聊天流。

3. 主动长期回忆检索

   当玩家追问“上次”“以前”“关系”“偏好”“前情 recap”“伏笔”这类长期回忆时，router 会按需暴露 `search_external_memory` 工具，让 LLM 主动检索 Honcho。

4. 玩家和角色 peer 隔离

   玩家 peer 主要保存偏好、节奏和互动风格。角色 peer 主要保存角色经历、关系、承诺、恐惧、伤痕和个人弧光。读取时如果当前玩家绑定了角色，会分别查询“玩家偏好视角”和“角色知识视角”。

5. 章节边界和旧章节检索

   当前章节默认可进入 prompt。旧章节应通过明确的回忆检索工具查询，避免污染每轮上下文预算。

6. 观测和排错

   `/dm token` 会显示外置记忆预算和最近读写观测指标，帮助判断是否启用、是否超时、是否重复跳过。

## 当前实现边界

当前版本采用“摘要/关键事件同步 + context 注入 + 按需检索工具”的模式。它已经为未来 hybrid/tools 模式留出接口，但没有让 Honcho 成为本地状态的权威来源。

Honcho dreams 相关能力暂时只接入本地安全契约：未来 dream 产出的玩家偏好、角色倾向、关系、recap 或 DM 风格洞察，必须先变成已脱敏、非权威、待审阅的建议。证据不足、置信度过低或不属于允许类型的内容会被丢弃。

## 部署前准备

Honcho 官方文档入口：

- [Honcho Quickstart](https://docs.honcho.dev/v3/documentation/introduction/quickstart)
- [Honcho SDK Reference](https://docs.honcho.dev/v3/documentation/reference/sdk)
- [Honcho self-hosting](https://docs.honcho.dev/v3/contributing/self-hosting)

本文配置矩阵基于这些官方语义：

- Python SDK 支持 `workspace_id`、`api_key`、`environment`、`base_url`、`timeout` 等初始化参数。
- Honcho Cloud 使用官方 API 地址 `https://api.honcho.dev`。
- self-hosting / Docker 默认不启用鉴权，对应官方环境变量 `AUTH_USE_AUTH=false`；开启鉴权后才需要 key。
- 官方本地服务示例默认监听 `http://localhost:8000`，健康检查是 `/health`，API 文档页是 `/docs`。

需要准备：

- 一个 Honcho workspace ID，例如 `paotuan-prod` 或 `paotuan-dev`。
- 一个目标：Honcho Cloud，或一个自建 Honcho 服务地址。
- Honcho API key：使用 Honcho Cloud 时需要；自建服务只有在开启鉴权时需要。
- AstrBot 运行时能安装 Python 依赖。
- 需要 API key 时，AstrBot 进程能读取保存 API key 的环境变量。

推荐命名约定：

- 一个机器人环境对应一个 Honcho workspace。
- Honcho session 按群、团或章节切分。
- 玩家 peer 和角色 peer 分开，避免把玩家偏好和角色知识混写。

## 部署步骤

1. 安装 Honcho SDK

   在运行 AstrBot 的同一个 Python 环境里执行：

   ```powershell
   python -m pip install honcho-ai
   ```

   如果 AstrBot 运行在容器、虚拟环境或 NAS 服务里，必须在那个环境里安装，而不是只在本机全局 Python 里安装。

2. 选择目标并设置 API key

   不要把真实 API key 写进仓库或插件配置文件。插件配置里只保存环境变量名。

   使用 Honcho Cloud 时，请设置云端 API key：

   Windows PowerShell 当前窗口临时设置：

   ```powershell
   $env:HONCHO_CLOUD_API_KEY="your-honcho-cloud-api-key"
   ```

   Windows 持久设置：

   ```powershell
   setx HONCHO_CLOUD_API_KEY "your-honcho-cloud-api-key"
   ```

   Linux / macOS shell：

   ```bash
   export HONCHO_CLOUD_API_KEY="your-honcho-cloud-api-key"
   ```

   使用 `setx`、系统服务配置、Docker 环境变量或 NAS 面板配置后，需要重启 AstrBot，让新环境变量进入进程。

   自建 Honcho Docker 默认无鉴权（官方 self-hosting 文档写明 `AUTH_USE_AUTH=false`）。这种场景可不设置 API key。若你在自建服务里开启了 `AUTH_USE_AUTH=true`，请另设一个自托管专用变量，例如：

   ```powershell
   $env:HONCHO_SELF_HOSTED_API_KEY="your-self-hosted-key"
   ```

   不建议让云端和自建服务共用同一个环境变量名。这样同时配置两套服务时，更容易明确知道当前 key 会发给哪一个目标。

3. 配置插件

   Honcho Cloud 示例：

   ```text
   honcho_enabled=true
   honcho_target=cloud
   honcho_workspace_id=paotuan-prod
   honcho_cloud_api_key_env=HONCHO_CLOUD_API_KEY
   honcho_base_url=
   honcho_environment=production
   honcho_timeout_seconds=8
   honcho_read_enabled=true
   honcho_write_enabled=true
   honcho_max_context_chars=1600
   honcho_assistant_peer_id=paotuan_dm
   honcho_cross_campaign_personalization_enabled=false
   ```

   自建 Docker 无鉴权示例：

   ```text
   honcho_enabled=true
   honcho_target=self_hosted
   honcho_workspace_id=paotuan-local
   honcho_base_url=http://localhost:8000
   honcho_self_hosted_auth_enabled=false
   honcho_self_hosted_api_key_env=
   honcho_environment=production
   honcho_timeout_seconds=8
   honcho_read_enabled=true
   honcho_write_enabled=true
   honcho_max_context_chars=1600
   honcho_assistant_peer_id=paotuan_dm
   honcho_cross_campaign_personalization_enabled=false
   ```

   自建 Docker 开启鉴权示例：

   ```text
   honcho_enabled=true
   honcho_target=self_hosted
   honcho_workspace_id=paotuan-prod
   honcho_base_url=https://honcho.example.com
   honcho_self_hosted_auth_enabled=true
   honcho_self_hosted_api_key_env=HONCHO_SELF_HOSTED_API_KEY
   honcho_environment=production
   honcho_timeout_seconds=8
   honcho_read_enabled=true
   honcho_write_enabled=true
   honcho_max_context_chars=1600
   honcho_assistant_peer_id=paotuan_dm
   honcho_cross_campaign_personalization_enabled=false
   ```

4. 重启 AstrBot 或重新加载插件

   重启后，插件初始化日志应能看到 Honcho 已启用。日志不会打印 API key。

5. 做一次测试团验证

   在一个测试会话里发几轮 `/dm` 消息，让插件产生关键事件或压缩摘要。然后尝试：

   ```text
   /dm 还记得上次我们答应了谁吗？
   /dm token
   ```

## 配置项说明

| 配置项 | 默认值 | 作用 | 建议 |
| --- | --- | --- | --- |
| `honcho_enabled` | `false` | 总开关。关闭时不会读取或写入 Honcho。 | 第一次部署前保持关闭；确认 SDK、API key 和 workspace 后再打开。 |
| `honcho_target` | `auto` | 选择 Honcho 目标：`auto`、`cloud`、`self_hosted`。 | 同时配置云端和 Docker 时显式设置为 `cloud` 或 `self_hosted`。 |
| `honcho_workspace_id` | 空 | Honcho workspace ID。 | 一个机器人环境一个 workspace，例如 `paotuan-prod`、`paotuan-dev`。 |
| `honcho_api_key_env` | `HONCHO_API_KEY` | 兼容旧配置的默认 API key 环境变量名。 | 新配置优先使用下面两个专用变量。 |
| `honcho_cloud_api_key_env` | 空 | Honcho Cloud API key 环境变量名。 | 建议设置为 `HONCHO_CLOUD_API_KEY`；留空时回退到 `honcho_api_key_env`。 |
| `honcho_base_url` | 空 | 自托管 Honcho API 地址。 | Docker 本地默认通常是 `http://localhost:8000`；`honcho_target=cloud` 时忽略。 |
| `honcho_self_hosted_auth_enabled` | `false` | 自托管服务是否启用 API key 鉴权。 | 对应官方 `AUTH_USE_AUTH`。本地 Docker 默认无鉴权，保持 `false`。 |
| `honcho_self_hosted_api_key_env` | 空 | 自托管 Honcho API key 环境变量名。 | 仅在 `honcho_self_hosted_auth_enabled=true` 时读取；建议不要和云端变量共用。 |
| `honcho_environment` | `production` | 传给 Honcho SDK 的 environment。 | 生产环境用 `production`。测试环境建议用单独 workspace 区分。 |
| `honcho_timeout_seconds` | `8` | 单次 Honcho 读写最长等待时间。 | 网络慢可调大。超时只影响本轮外置记忆，不影响本地跑团。 |
| `honcho_read_enabled` | `true` | 是否在 prompt 前读取 Honcho context。 | 排查 prompt 预算或内容污染时可单独关闭。 |
| `honcho_write_enabled` | `true` | 是否把受控摘要和关键事件写入 Honcho。 | 只想试读不想写入时关闭。 |
| `honcho_max_context_chars` | `1600` | 每轮注入 prompt 的 Honcho context 最大字符数。 | 小模型或高频群聊可调低；需要更多 recap 时可调高。 |
| `honcho_assistant_peer_id` | `paotuan_dm` | Honcho 里代表 paotuan DM 的 peer ID。 | 多个机器人共用 workspace 时应使用不同 ID。 |
| `honcho_cross_campaign_personalization_enabled` | `false` | 是否允许同一玩家的偏好跨团复用。 | 默认关闭。打开后只复用玩家级偏好，不复用角色事实或剧情事实。 |

## 配置状态矩阵

Honcho 配置按四个维度判断：总开关、目标选择、目标地址、鉴权变量。

| 状态 | `honcho_enabled` | `honcho_target` | `honcho_base_url` | 鉴权配置 | 行为 |
| --- | --- | --- | --- | --- | --- |
| 未启用 | `false` | 任意 | 任意 | 任意 | 总开关优先，不导入 SDK，不调用云端或本地服务，返回 `honcho_disabled`。 |
| 未配置 workspace | `true` | 任意 | 任意 | 任意 | 返回 `honcho_workspace_missing`，本地跑团继续。 |
| `auto` + 未填本地地址 | `true` | `auto` | 空 | `honcho_cloud_api_key_env` 或 `honcho_api_key_env` 存在 | 选择 Honcho Cloud，传 `https://api.honcho.dev` 和云端 key。 |
| `auto` + 已填本地地址 | `true` | `auto` | 自建地址 | 无鉴权 | 选择 self-hosted，传自建地址，不传 `api_key`。 |
| 强制 Cloud | `true` | `cloud` | 空或自建地址 | 云端 key 存在 | 使用 Honcho Cloud；即使填了 Docker 地址也忽略本地地址。 |
| Cloud 缺 key | `true` | `cloud` 或 `auto` 选中 Cloud | 空 | key 不存在 | 返回 `honcho_api_key_missing`，不会尝试自建地址。 |
| 强制 self-hosted 无鉴权 | `true` | `self_hosted` | 自建地址 | `honcho_self_hosted_auth_enabled=false` | 使用自建地址，不读取也不发送云端 key。 |
| 强制 self-hosted 但缺地址 | `true` | `self_hosted` | 空 | 任意 | 返回 `honcho_base_url_missing`。 |
| self-hosted 开启鉴权且有 key | `true` | `self_hosted` 或 `auto` 选中 self-hosted | 自建地址 | `honcho_self_hosted_auth_enabled=true` 且 key 存在 | 使用自建地址，同时传自托管 key。 |
| self-hosted 开启鉴权但缺 key | `true` | `self_hosted` 或 `auto` 选中 self-hosted | 自建地址 | `honcho_self_hosted_auth_enabled=true` 但 key 不存在 | 返回 `honcho_api_key_missing`。 |
| 同时配置云端和 Docker，但未启用 | `false` | 任意 | 自建地址 | 云端/本地 key 可同时存在 | 总开关优先，不调用任何目标。 |
| 同时配置云端和 Docker，启用 Cloud | `true` | `cloud` | 自建地址 | 云端 key 存在 | 只用 Cloud；不会把自建地址传给 SDK。 |
| 同时配置云端和 Docker，启用 self-hosted | `true` | `self_hosted` | 自建地址 | 本地鉴权按开关决定 | 只用 self-hosted；无鉴权时不会把云端 key 发给本地服务。 |
| 非法目标值 | `true` | 其它字符串 | 任意 | 任意 | 返回 `honcho_target_invalid`。 |

`honcho_base_url` 会做轻量规范化：`localhost:8000/` 会按 `http://localhost:8000` 传给 SDK，尾部斜杠会去掉。官方 Docker Compose 示例默认把 API 绑定到本机 `127.0.0.1:8000`，常用健康检查是 `http://localhost:8000/health`，API 文档页是 `http://localhost:8000/docs`。

从旧配置升级时注意：旧的 `honcho_api_key_env` 仍然作为 fallback 保留，但 self-hosted 是否发送 key 由 `honcho_self_hosted_auth_enabled` 决定。如果你原来用自建 Honcho 且开启了鉴权，需要显式设置 `honcho_self_hosted_auth_enabled=true`；如果是 Docker 默认无鉴权，保持默认 `false`，避免把 Cloud key 发给本地服务。

## 如何使用

普通跑团时不需要新命令。玩家继续用自然语言和 `/dm` 互动，插件会在后台读写外置记忆。

适合触发长期回忆检索的说法：

```text
/dm 还记得上次我们答应了酒馆老板什么吗？
/dm 我以前和这个 NPC 的关系怎么样？
/dm 上一章最后留下了哪些伏笔？
/dm 你记得我更喜欢细节多一点还是节奏快一点吗？
```

这些请求会让 router 倾向于暴露 `search_external_memory`。工具返回内容只作非权威回忆线索；如果它和本地状态、工具结果或规则执行冲突，必须以本地结果为准。

写入 Honcho 的内容主要来自：

- 叙事轨迹里的关键行动和结果。
- 本地 memory compression 后的摘要。
- 玩家偏好、角色倾向、关系、recap、DM 风格反馈这类受控摘要。

写入前会按 `paotuan.external_memory.v1` 契约生成 metadata，并对正文、检索 query 和外发 ID 做安全处理：平台玩家 ID、群 ID 等会变成稳定 pseudonym；本机路径、服务器路径、API key、token、prompt 和 audit 片段会被替换为 `[redacted]`。

外置写入还会记录短 `source_event_id` 标记，避免同一关键事件或同一压缩摘要在重试时重复写入 Honcho。这个标记只用于幂等控制，不包含原文。

## 如何验证

用 `/dm token` 查看本地诊断。外置记忆观测信息只记录安全指标，例如：

- 读取/写入是否成功。
- 错误类型。
- context 字符数。
- 是否截断。
- 是否重复跳过。
- 是否出现状态敏感线索。

诊断不会把 Honcho context、写入正文、完整 prompt、原始 audit、凭证或原始平台 ID 复制进本地 audit 或 `/dm token` 输出。

建议测试流程：

1. 打开 `honcho_enabled=true`、`honcho_read_enabled=true`、`honcho_write_enabled=true`。
2. 在测试团里进行几轮正常 `/dm` 互动。
3. 用 `/dm token` 查看外置记忆预算和观测指标。
4. 问一个长期回忆问题，例如“还记得上次我们答应了谁吗？”。
5. 如果想确认降级能力，临时关闭 `honcho_read_enabled` 或 `honcho_enabled`，确认本地跑团仍能继续。

## 常见问题

| 现象 / 错误 | 含义 | 处理方式 |
| --- | --- | --- |
| `honcho_disabled` | `honcho_enabled=false`。 | 打开 `honcho_enabled`，或保持关闭作为本地 JSON 模式。 |
| `honcho_read_disabled` | 读取开关关闭。 | 打开 `honcho_read_enabled`。 |
| `honcho_write_disabled` | 写入开关关闭。 | 打开 `honcho_write_enabled`。 |
| `honcho_workspace_missing` | 没有配置 workspace。 | 设置 `honcho_workspace_id`。 |
| `honcho_target_invalid` | `honcho_target` 不是 `auto`、`cloud`、`self_hosted`。 | 改成三者之一。需要明确使用云端或本地时，不要留给隐式推断。 |
| `honcho_base_url_missing` | 已选择 `self_hosted`，但没有填写自建服务地址。 | 设置 `honcho_base_url`，例如 `http://localhost:8000`。 |
| `honcho_api_key_missing` | Cloud 模式，或开启鉴权的 self-hosted 模式下环境变量不存在。 | Cloud 检查 `honcho_cloud_api_key_env`；self-hosted 检查 `honcho_self_hosted_api_key_env`；确认后重启 AstrBot。 |
| `honcho_sdk_missing` | AstrBot Python 环境里没有安装 `honcho-ai`。 | 在同一个 Python 环境执行 `python -m pip install honcho-ai`。 |
| `honcho_timeout` | Honcho 调用超过 `honcho_timeout_seconds`。 | 检查网络或调大超时；本轮跑团会继续走本地状态。 |
| `honcho_call_failed` | SDK 或 Honcho 服务返回异常。 | 检查 `honcho_target`、`honcho_base_url`、workspace、API key 权限和 Honcho 服务状态。 |
| context 太长或效果不稳定 | 外置记忆占用 prompt 预算或返回内容太多。 | 调低 `honcho_max_context_chars`，或临时关闭 `honcho_read_enabled`。 |
| 记忆“串团” | 跨团个性化或 workspace/session 规划不当。 | 保持 `honcho_cross_campaign_personalization_enabled=false`；按环境拆 workspace，按团/章节拆 session。 |

自建 Docker 排障建议：

- 先访问 `http://localhost:8000/health`。如果 AstrBot 不在同一台机器或同一容器网络里，`localhost` 可能指向 AstrBot 自己，而不是 Honcho。
- 再访问 `http://localhost:8000/docs`，确认 API 服务已经启动。
- 如果自建服务未开启鉴权，保持 `honcho_self_hosted_auth_enabled=false`，避免把云端 key 发给本地服务。
- 如果同时填了 Cloud key 和 Docker 地址，请显式设置 `honcho_target=cloud` 或 `honcho_target=self_hosted`，不要依赖 `auto` 做长期生产配置。

## 回滚方式

手动回滚不需要迁移数据。关闭任一开关即可：

```text
honcho_enabled=false
```

或者只关闭读取 / 写入：

```text
honcho_read_enabled=false
honcho_write_enabled=false
```

关闭后插件会回到本地 JSON 行为，已有本地存档不依赖 Honcho。Honcho 中已有的外置记忆不会被自动删除。

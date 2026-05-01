# AstrBot Auto TRPG DM

基于 AstrBot v4.5.7+ 的全自然语言 TRPG DM 插件。

这个项目的目标不是做一组分散的命令，而是把 AstrBot 作为消息入口和 LLM 能力层，在插件内部实现一个可持续运行的小型 TRPG runtime。玩家直接说“我靠墙潜行过去再射最近的敌人”，系统再根据当前模式、角色状态、战场事实和本地规则工具完成裁定。

当前版本重点能力：

- 全自然语言跑团，不要求玩家记忆 `/车卡`、`/move`、`/attack` 之类命令。
- Intent Router 按场景动态挂载工具，尽量只暴露当前真正需要的能力。
- 支持多步工具调用循环，优先走 AstrBot Function Calling，不支持时可回退解析 `{"tool_calls":[...]}`。
- 角色卡、世界设定、场景状态使用 Tag 型无模式结构，便于逐步补完和长期演化。
- 内置本地战棋空间引擎，负责坐标、移动、障碍、视线、掩体和攻击距离校验。
- 内置规则运行时，可注册和执行受限 Python 规则函数。
- 新增 DND 2024 本地核心规则书检索层，用于按需查询规则摘要，而不是把整本规则塞进 prompt。
- 新增 DM guidance 规则检索，帮助 agent 在“怎么裁定、怎么描述、怎么控制节奏”上更稳。
- 可选接入 Honcho 外置记忆，用受控摘要和关键事件增强长期回忆；默认关闭，不影响本地 JSON 存档。

## 适合什么场景

这个插件比较适合下面几类玩法：

- 纯文字团，希望玩家像聊天一样行动和追问。
- 带轻战棋或位置概念的团，需要明确移动、视线、距离和轮次。
- 规则并不固定死板，但你希望数值结算和核心物理事实尽量稳定。
- 想让 AI 作为 DM / 协同 DM，而不是只做“会说话的骰子机器人”。

## 核心设计

### 1. Intent Router 驱动

`[core/router.py](/F:/paotuan/astrbot_plugin_auto_trpg_dm/core/router.py)` 是自然语言入口。它会：

1. 读取当前会话与战斗状态。
2. 判断当前模式，例如叙事、角色创建、规则创作、战棋、结算。
3. 只挂载当前模式允许的工具，避免无关工具膨胀上下文。
4. 调用 `context.llm_generate()` 驱动多步工具循环。
5. 保存审计、状态更新和最终回复。

### 2. 本地工具负责“事实”

这个项目尽量把确定性的部分从 LLM 手里拿出来：

- 坐标、阻挡、路径、视线、攻击范围：交给 `spatial/`。
- 轮次推进、超时、自动保守行动：交给 `turn_tools.py`。
- 角色卡、场景、世界设定和会话控制：交给 `memory_tools.py`。
- 规则检定、伤害、豁免等数值执行：交给 `rule_tools.py` 和 `rules/python_runtime.py`。
- DND 2024 规则摘要和 DM 指引：交给 `rulebook_tools.py`。

这样 LLM 更像“裁定与叙事协调层”，而不是直接篡改底层事实。

### 3. 规则知识与数值执行分离

这部分是当前版本非常重要的变化。

- `query_core_rules`：查询 DND 2024 核心规则摘要、动作经济、状态、施法通用规则、装备通用规则，以及 DM guidance。
- `execute_rule`：负责真正的骰子、命中、豁免、伤害、治疗和其他数值结算。

规则书内容不会被长期写进 `session`、`memory_summary` 或 `system prompt`。项目倾向于“按需查规则，再做裁定”，而不是把大段规则文本一直挂在上下文里。

### 4. Honcho 外置记忆增强

Honcho 接入是可选增强层，不替代本地 `GameSession` 和 `JsonGameRepository`。本地 JSON 仍然是 HP、物品、位置、回合、规则结果等强一致事实的权威来源；Honcho 只提供玩家偏好、角色倾向、幕间 recap、伏笔和关键事件这类辅助回忆。

接入后能带来的能力：

- 每轮回复前读取 Honcho context，并作为单独的“外部 Honcho 辅助记忆”段落注入 prompt。
- 本地完成叙事轨迹或压缩摘要后，把受控摘要和关键事件写入 Honcho。
- 玩家追问“上次”“以前”“关系”“偏好”“前情 recap”“伏笔”时，router 会按需暴露 `search_external_memory` 工具，让 LLM 主动检索长期记忆。
- 玩家 peer 和角色 peer 分开：玩家 peer 保存偏好、节奏和互动风格；角色 peer 保存角色经历、关系、承诺、伤痕、恐惧和个人弧光。
- 当前章节默认进入 prompt；旧章节通过明确的回忆检索工具查询，避免污染每轮上下文预算。
- `/dm token` 会显示外置记忆预算和最近读写观测指标，便于判断是否启用、是否超时、是否重复跳过。

它不会做这些事：

- 不把 Honcho 当作 HP、物品、位置、回合、规则结果或骰子结果的权威来源。
- 不写完整原始聊天流、完整 prompt、原始 audit、凭证、服务器路径或本地环境隐私。
- 不要求安装 Honcho 才能跑团。Honcho 不可用、未安装 `honcho-ai`、缺少 API key 或请求超时时，本地跑团继续运行。
- 不自动使用 Honcho dreams 改写本地状态。当前只保留本地安全契约：未来 dream 产出的玩家偏好、角色倾向、关系、recap 或 DM 风格洞察，必须先变成已脱敏、非权威、待审阅的建议。

#### 部署 Honcho

Honcho 官方文档入口：

- [Honcho Quickstart](https://docs.honcho.dev/v3/documentation/introduction/quickstart)
- [Honcho SDK Reference](https://docs.honcho.dev/v3/documentation/reference/sdk)
- [Honcho self-hosting](https://docs.honcho.dev/v3/contributing/self-hosting)

部署步骤：

1. 在 Honcho 控制台创建 API key。生产环境建议使用 `environment=production`。
2. 在运行 AstrBot 的同一个 Python 环境里安装 SDK：

   ```powershell
   python -m pip install honcho-ai
   ```

3. 把 API key 放到 AstrBot 进程能读取的环境变量里，不要写进仓库或插件配置文件。

   Windows PowerShell 当前窗口临时设置：

   ```powershell
   $env:HONCHO_API_KEY="your-honcho-api-key"
   ```

   Windows 持久设置：

   ```powershell
   setx HONCHO_API_KEY "your-honcho-api-key"
   ```

   Linux / macOS shell：

   ```bash
   export HONCHO_API_KEY="your-honcho-api-key"
   ```

   使用 `setx` 或系统服务配置后，需要重启 AstrBot，让新环境变量进入进程。

4. 在 AstrBot 插件配置里启用 Honcho：

   ```text
   honcho_enabled=true
   honcho_workspace_id=paotuan-prod
   honcho_api_key_env=HONCHO_API_KEY
   honcho_base_url=
   honcho_environment=production
   honcho_timeout_seconds=8
   honcho_read_enabled=true
   honcho_write_enabled=true
   honcho_max_context_chars=1600
   honcho_assistant_peer_id=paotuan_dm
   honcho_cross_campaign_personalization_enabled=false
   ```

5. 重启 AstrBot 或重新加载插件，然后在一个测试团里发几轮 `/dm` 消息，让插件产生关键事件或压缩摘要。
6. 用 `/dm token` 查看外置记忆预算和观测摘要。如果 Honcho 正常工作，后续追问“还记得上次我们答应了谁吗？”这类问题时，router 会按需检索长期记忆。

自托管 Honcho 时，把 `honcho_base_url` 设置为你的服务地址，例如 `http://localhost:8000` 或内网 HTTPS 地址；Honcho Cloud 通常留空。

#### 配置项

| 配置项 | 默认值 | 作用 | 建议 |
| --- | --- | --- | --- |
| `honcho_enabled` | `false` | 总开关。关闭时不会读取或写入 Honcho。 | 第一次部署前保持关闭；确认 SDK、API key 和 workspace 后再打开。 |
| `honcho_workspace_id` | 空 | Honcho workspace ID。 | 一个机器人环境一个 workspace，例如 `paotuan-prod`、`paotuan-dev`。 |
| `honcho_api_key_env` | `HONCHO_API_KEY` | 保存 API key 的环境变量名。 | 只写变量名，不写真实 key。 |
| `honcho_base_url` | 空 | 自托管 Honcho API 地址。 | 使用 Honcho Cloud 时留空；自托管时填写完整 base URL。 |
| `honcho_environment` | `production` | 传给 Honcho SDK 的 environment。 | 生产环境用 `production`，测试环境可用单独 workspace 区分。 |
| `honcho_timeout_seconds` | `8` | 单次 Honcho 读写最长等待时间。 | 网络慢可调大；超时只影响本轮外置记忆，不影响本地跑团。 |
| `honcho_read_enabled` | `true` | 是否在 prompt 前读取 Honcho context。 | 排查 prompt 预算或内容污染时可单独关闭。 |
| `honcho_write_enabled` | `true` | 是否把受控摘要和关键事件写入 Honcho。 | 只想试读不想写入时关闭。 |
| `honcho_max_context_chars` | `1600` | 每轮注入 prompt 的 Honcho context 最大字符数。 | 小模型或高频群聊可调低；需要更多 recap 时可调高。 |
| `honcho_assistant_peer_id` | `paotuan_dm` | Honcho 里代表 paotuan DM 的 peer ID。 | 多个机器人共用 workspace 时应使用不同 ID。 |
| `honcho_cross_campaign_personalization_enabled` | `false` | 是否允许同一玩家的偏好跨团复用。 | 默认关闭。打开后只复用玩家级偏好，不复用角色事实或剧情事实。 |

#### 如何使用

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

- 叙事轨迹里的关键行动和结果；
- 本地 memory compression 后的摘要；
- 玩家偏好、角色倾向、关系、recap、DM 风格反馈这类受控摘要。

写入前会按 `paotuan.external_memory.v1` 契约生成 metadata，并对正文、检索 query 和外发 ID 做安全处理：平台玩家 ID、群 ID 等会变成稳定 pseudonym；本机路径、服务器路径、API key、token、prompt 和 audit 片段会被替换为 `[redacted]`。外置写入还会记录短 `source_event_id` 标记，避免同一关键事件或同一压缩摘要在重试时重复写入 Honcho；这个标记只用于幂等控制，不包含原文。

#### 如何验证和排错

先用 `/dm token` 看本地诊断。外置记忆观测信息只记录安全指标，例如读取/写入是否成功、错误类型、context 字符数、是否截断、是否重复跳过和是否出现状态敏感线索；不会把 Honcho context、写入正文、完整 prompt、原始 audit、凭证或原始平台 ID 复制进本地 audit 或 `/dm token` 诊断。

常见问题：

| 现象 / 错误 | 含义 | 处理方式 |
| --- | --- | --- |
| `honcho_disabled` | `honcho_enabled=false`。 | 打开 `honcho_enabled`，或保持关闭作为本地 JSON 模式。 |
| `honcho_read_disabled` | 读取开关关闭。 | 打开 `honcho_read_enabled`。 |
| `honcho_write_disabled` | 写入开关关闭。 | 打开 `honcho_write_enabled`。 |
| `honcho_workspace_missing` | 没有配置 workspace。 | 设置 `honcho_workspace_id`。 |
| `honcho_api_key_missing` | 环境变量不存在或 AstrBot 进程读不到。 | 确认 `honcho_api_key_env` 指向的变量存在；重启 AstrBot。 |
| `honcho_sdk_missing` | AstrBot Python 环境里没有安装 `honcho-ai`。 | 在同一个 Python 环境执行 `python -m pip install honcho-ai`。 |
| `honcho_timeout` | Honcho 调用超过 `honcho_timeout_seconds`。 | 检查网络或调大超时；本轮跑团会继续走本地状态。 |
| `honcho_call_failed` | SDK 或 Honcho 服务返回异常。 | 检查 `honcho_base_url`、workspace、API key 权限和 Honcho 服务状态。 |
| context 太长或效果不稳定 | 外置记忆占用 prompt 预算或返回内容太多。 | 调低 `honcho_max_context_chars`，或临时关闭 `honcho_read_enabled`。 |
| 记忆“串团” | 跨团个性化或 workspace/session 规划不当。 | 保持 `honcho_cross_campaign_personalization_enabled=false`；按环境拆 workspace，按团/章节拆 session。 |

手动回滚很直接：关闭 `honcho_enabled`，或只关闭 `honcho_read_enabled` / `honcho_write_enabled`。关闭后插件会回到本地 JSON 行为，已有本地存档不依赖 Honcho。

## 当前能力一览

### 自然语言状态与角色管理

- 创建和绑定角色。
- 用自然语言补充角色 Tag，例如职业、装备、风格、默认战斗行为。
- 维护场景状态、世界设定、长期剧情钩子。
- 会话重置、备份、恢复和压缩。

### 战棋与轮次

- 创建网格地图。
- 放置实体。
- 校验移动。
- 校验攻击向量、距离、视线和掩体。
- 管理轮次、行动顺序和结算阶段。
- 对多人团增加控制权约束和 120 秒超时后的保守自动行动。

### 规则与裁定

- 注册本地规则函数。
- 执行检定、伤害、治疗等规则。
- 查询 DND 2024 本地规则卡。
- 查询 DM guidance，例如“何时临时裁定、如何处理失败推进、如何保持不是和玩家对抗”。

### 视觉地图

- 可按上下文需要生成 SVG 地图或战场示意。
- 视觉地图只做展示，不直接改写战棋物理事实。

## 目录结构

```text
astrbot_plugin_auto_trpg_dm/
  main.py
  core/
    router.py
    modes.py
    models.py
    prompts.py
    security.py
  tools/
    registry.py
    memory_tools.py
    spatial_tools.py
    turn_tools.py
    rule_tools.py
    rulebook_tools.py
    map_tools.py
  rules/
    python_runtime.py
    validator.py
    dice.py
  rulebook/
    models.py
    store.py
    retriever.py
    seed/
      dnd2024_core/
  spatial/
    grid.py
    engine.py
    los.py
  storage/
    json_repository.py
tests/
scripts/
```

## DND 2024 规则书集成

当前仓库已经接入一版本地规则书检索。

规则书能力的思路是：

- 离线整理规则资料，生成结构化规则卡。
- 运行时本地读取 `rule_cards.jsonl`、别名表和索引。
- 玩家问到状态、动作、豁免、优势/劣势、生命值归零、施法通用规则等内容时，优先调用 `query_core_rules`。
- 工具只返回少量摘要、流程、例外和来源路径，不返回大段原文。

相关文件：

- [rulebook_tools.py](/F:/paotuan/astrbot_plugin_auto_trpg_dm/tools/rulebook_tools.py)
- [registry.py](/F:/paotuan/astrbot_plugin_auto_trpg_dm/tools/registry.py)
- [DND2024_CORE_RULEBOOK_INTEGRATION_PLAN.md](/F:/paotuan/DND2024_CORE_RULEBOOK_INTEGRATION_PLAN.md)
- [build_dnd2024_core_rulebook.py](/F:/paotuan/scripts/build_dnd2024_core_rulebook.py)

这套设计的重点是上下文成本可控、来源可追溯、不会污染跑团存档。

## 运行与部署

### 本地开发

把插件目录放入 AstrBot 插件目录后，插件入口是：

```text
astrbot_plugin_auto_trpg_dm/main.py
```

运行数据会写入 AstrBot 数据目录，例如：

```text
data/plugin_data/astrbot_plugin_auto_trpg_dm/
  saves/
  rules/
  audit/
  maps/
  rulebooks/
```

LLM 调用不直接依赖 `requests` 或 `openai` SDK，而是通过 AstrBot 提供的：

```python
await self.context.llm_generate(...)
```

### NAS 部署

仓库已经带有一套本地部署脚本，设计目标是“方便，但别把脏工作区和私钥一起送上去”。

相关文件：

- [deploy-nas.ps1](/F:/paotuan/scripts/deploy-nas.ps1)
- [nas-deploy.example.json](/F:/paotuan/scripts/nas-deploy.example.json)

特点：

- 只部署当前 Git `HEAD`。
- 工作区不干净时默认拒绝部署。
- 部署前运行 `compileall`，本地装了 `pytest` 时会顺便跑测试。
- 用 `git archive` 打包，不带 `.deploy/`、缓存、运行数据和临时文件。
- NAS 端先备份旧插件目录，再替换新版本。

初始化本地配置：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy-nas.ps1 -Init
```

配置好 `.deploy/nas-deploy.json` 后部署：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy-nas.ps1
```

## PR 流程

仓库已经补了一套比较轻量的 PR 工作流。

- GitHub Actions 会在 PR / push 时运行基础检查。
- 本地可以用 [handle-pr.ps1](/F:/paotuan/scripts/handle-pr.ps1) 辅助拉取、检查、合并和部署 PR。

检查 PR：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/handle-pr.ps1 -PrNumber 123
```

检查通过后合并：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/handle-pr.ps1 -PrNumber 123 -Merge
```

合并并部署到 NAS：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/handle-pr.ps1 -PrNumber 123 -Merge -DeployAfterMerge
```

## 测试

当前测试主要覆盖这些方向：

- 轮次控制与超时推进。
- 角色卡与 Tag 处理。
- 规则书检索。
- 规则书工具调用。
- DM guidance / 安全策略。
- Honcho 外置记忆的可选降级、ID 映射、prompt 注入和受控写入 payload。

如果本地环境可用，建议运行：

```powershell
python -m compileall -q astrbot_plugin_auto_trpg_dm tests
python -m pytest -q
```

## 版本更新

当前插件版本：

```text
v0.1.70
```

本轮更新重点：

- 新增 Honcho 外置记忆 MVP：受控摘要/关键事件同步、prompt context 注入、默认关闭和失败降级。
- 接入 DND 2024 本地核心规则书检索与规则卡种子数据。
- 新增 `query_core_rules`，可按模式挂载到叙事、战棋、规则创作和结算流程。
- 增加 DM guidance 检索，帮助 agent 在规则不确定、需要临时裁定或控制叙事节奏时更稳。
- 强化安全策略与 prompt 约束，减少越权控制、状态污染和规则编造。
- 扩展路由与工具注册逻辑，让不同消息类型拿到更贴合的工具集合。
- 增补规则书、DM guidance 和安全相关测试。

## 说明

这个项目仍然是一个偏工程化、快速迭代的 TRPG runtime，不是“全自动完美 DM”。它更像一套把自然语言、状态机、战棋事实、规则执行和本地知识层拼起来的可成长骨架。

如果你想做的是：

- 更稳的 AI 跑团裁定
- 更少 prompt 污染
- 更强的多人回合约束
- 更像长期运营项目而不是一次性 demo

这套结构会比把所有规则和状态都硬塞进一个大 prompt 更耐用。

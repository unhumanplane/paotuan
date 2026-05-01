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

Honcho 接入是可选增强层，不替代本地 `GameSession` 和 `JsonGameRepository`。本地 JSON 仍然是 HP、物品、位置、回合、规则结果等强一致事实的权威来源；Honcho 只提供玩家偏好、角色倾向、幕间 recap 和关键事件这类辅助回忆。

第一版采用“摘要/关键事件同步 + context 注入”：

- prompt 前读取 Honcho context，并作为单独的“外部 Honcho 辅助记忆”段落注入。
- 本地完成叙事轨迹或压缩摘要后，把受控摘要写入 Honcho。
- 不写完整原始聊天流、完整 prompt、原始 audit、凭证、服务器路径或本地环境隐私。
- Honcho 不可用、未安装 `honcho-ai`、缺少 API key 或请求超时时，本地跑团继续运行，并写入受控 audit 诊断。

启用时，需要在 AstrBot 运行环境安装 Honcho SDK：

```powershell
pip install honcho-ai
```

然后通过插件配置设置：

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

真实 API key 应放在环境变量里，例如 `HONCHO_API_KEY`，不要写进仓库或插件配置文件。建议一个机器人环境对应一个 Honcho workspace，Honcho session 按群、团或章节切分；玩家 peer 和角色 peer 分开，避免把玩家偏好和角色知识混写。

通常只需要配置 `honcho_enabled`、`honcho_workspace_id` 和环境变量里的 API key。`honcho_base_url` 只在自托管 Honcho 时填写；`honcho_environment` 用于区分 production/dev；`honcho_timeout_seconds` 控制单次外置记忆调用的最长等待时间；`honcho_assistant_peer_id` 是 Honcho 里代表 paotuan DM 的 peer。

`honcho_cross_campaign_personalization_enabled` 默认关闭。关闭时，同一玩家在不同 campaign 会得到不同 player peer，避免跨团串味；打开后只建议复用玩家级偏好，例如节奏、详细程度、战斗复杂度接受度、谜题偏好和 recap 风格，不复用角色事实或剧情事实。

写入 Honcho 前会按 `paotuan.external_memory.v1` 契约生成 metadata，并对正文、检索 query 和外发 ID 做安全处理：平台玩家 ID、群 ID 等会变成稳定 pseudonym；本机路径、服务器路径、API key、token、prompt 和 audit 片段会被替换为 `[redacted]`。这保证 Honcho 侧只拿到可用于长期回忆的跑团投影，而不是本地账号、部署环境或原始调试材料。

外置写入还会记录短 `source_event_id` 标记，用来避免同一关键事件或同一压缩摘要在重试时重复写入 Honcho；这个标记只用于幂等控制，不包含原文。

读取 Honcho context 时，插件会把记忆标成“可用回忆线索”或“状态敏感线索”。玩家偏好、关系、recap 和伏笔可以辅助叙事；涉及 HP、物品、位置、轮次、规则或骰子的外部记忆只能当历史线索，不能覆盖当前本地状态和工具结果。

当玩家明确追问“上次”“以前”“关系”“偏好”“前情 recap”或“伏笔”这类长期回忆时，router 还会按需暴露 `search_external_memory` 工具，让 LLM 主动检索 Honcho。这个工具不会修改本地状态；工具返回给 LLM 的正文不会原样写进本地 audit。

玩家和角色会使用不同 Honcho peer：玩家 peer 主要保存偏好、节奏和互动风格，角色 peer 主要保存角色经历、关系和个人弧光。读取时如果当前玩家绑定了角色，会分别查询“玩家偏好视角”和“角色知识视角”；写入关键事件时，玩家行动摘要写入玩家 peer，角色经历/裁定写入角色 peer。

Honcho dreams 相关能力暂时只接入本地安全契约：未来 dream 产出的玩家偏好、角色倾向、关系、recap 或 DM 风格洞察，必须先变成已脱敏、非权威、待审阅的建议；证据不足、置信度过低或不属于允许类型的内容会被丢弃。

每次外置记忆读取还会附带 campaign lifecycle scope，包括 workspace、campaign、chapter、phase、Honcho session 和 peer IDs。当前 prompt 默认只取当前章节；旧章节应通过明确的回忆检索工具查询，避免污染当前回合预算。

外置记忆的观测信息只记录安全指标，例如读取/写入是否成功、错误类型、context 字符数、是否截断、是否重复跳过和是否出现状态敏感线索；不会把 Honcho context、写入正文、完整 prompt、原始 audit、凭证或原始平台 ID 复制进本地 audit 或 `/dm token` 诊断。手动回滚很直接：关闭 `honcho_enabled` 或关闭读写开关后，跑团会回到本地 JSON 行为。

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

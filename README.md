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
- 可选接入 TRPG 氛围图片生成，用剧情节奏触发的图片辅助理解场景；默认关闭，不接受玩家直接命令生图。

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

完整部署、配置、使用和排错说明见 [Honcho 外置记忆接入指南](docs/honcho-external-memory.md)。

### 5. 氛围图片生成

氛围图片是可选视觉辅助，和 SVG 战棋地图是两套功能。SVG 地图用于位置、距离、视线和战场示意；氛围图只用于渲染剧情气氛、帮助玩家理解关键场景，不会写入任何权威游戏事实。

当前接入目标是 PackyAPI `gpt-image-2`，默认走 `/v1/images/generations`，也可以切换到 `/v1/chat/completions`。图片 API key、base URL、模型、尺寸、质量、返回格式、频率和 prompt 模板都通过 AstrBot 插件配置设置，默认关闭。prompt 模型会先返回 `title`、`prompt`、`style`，插件再拼合成最终生图 prompt。完整配置、触发规则、隐私边界和费用风险见 [TRPG 氛围图片生成指南](docs/ambient-image-api.md)。

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
  ambient_images/
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
- 氛围图片 API 的关闭、缺 key、双 endpoint 解析、触发门禁、prompt 保存和 pending 图片输出。

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

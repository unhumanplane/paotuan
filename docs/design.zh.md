# 设计文档：双 Agent TRPG 架构

## 概述

本文档定义了将当前单体 TRPG DM 拆分为两个职责清晰的 Agent 的架构，覆盖游戏全生命周期的三个场景。

## Agent 定义

### DM Agent（创意导演）
- **可见**：完整游戏状态、玩家输入、BASE_RULES、所有历史 RA 摘要
- **负责**：叙事、氛围、对话、故事方向、玩家意图解读
- **行为**：调用所有工具、定义游戏规则、决定周期开始/结束
- **输出**：面向玩家的叙事文本 + 改变游戏状态的工具调用
- **不做**：结构化记账、数值计算、机器可读的状态差异

### RA — Recorder Agent（状态规范化器）
- **可见**：`ra_cycle_input` 过滤投影 + 清洗后的权威字段快照 + BASE_RULES。RA 不接收完整 `GameSession`。
  - **RA 输入 allowlist**（明确允许）：`dm_narrative`、清洗后的 `tools_called` 结果、`character_status` 权威字段、`scene` 公共字段、`world_tags`、`rule_sets`、`BASE_RULES`
  - **RA 输入 blocklist**（默认不传）：`player_message`、玩家原始输入、DM 隐藏备注、System Prompt、诊断字段（token 消耗、debug 日志）、PII（display name 等可识别信息）
- **负责**：结构化数据生成、数值规范化、一致性记录、周期摘要
- **行为**：读取过滤后的 DM 输出和工具结果，生成结构化 JSON，由框架校验并应用 allowlisted 补丁
- **输出**：机器可读的结构化 JSON（从不直接发给玩家）
- **不做**：创意决策、叙事生成、工具调用、推翻 DM 叙事

**黄金法则**：RA 区分"叙事字段"与"权威结构化字段"。
- **叙事字段**（场景描述、氛围、NPC 反应、对话内容）—— 跟随 DM 叙事，DM 拥有最终解释权。
- **权威结构化字段**（HP、MP、alive、position、inventory、conditions、quest state、资源数量等）—— **必须以 tool trace、validator 结果和状态迁移结果为准**。如果 DM 说"地精倒下"但 `execute_rule` 返回还剩 4 HP，则 `hp=4, alive=true`，同时在 `discrepancies` 中记录冲突：`"DM 叙事描述敌人倒下，但工具结算显示 hp=4"`。
- RA **不覆盖** tool trace 给出的权威数值，但**保留** DM 叙事文本作为场景描述。
- **叙事圆回责任**：DM Agent 在下一周期发现 `discrepancies` 非空时，必须在叙事中用合理的场内解释圆回冲突。例如把"倒下"解释为踉跄、被击退、失衡或装死；若无法合理化，应简短更正上一段叙事，确保玩家可见叙事与权威结构化状态最终一致。

## 共享常量

### BASE_RULES（基础规则）
注入两个 Agent 的 System Prompt 的常量文档，包含：
- 元机制（例如"本游戏使用 d20 骰子系统"）
- 禁止行为（禁止 OOC、禁止元游戏）
- 通用约束（未经同意禁止 PvP、开场后禁止改写剧情）

游戏规则由 DM Agent 在游戏过程中定义，并通过 DM/工具输出留下证据。RA 可以基于这些证据提出结构化 `rule_sets` 补丁；框架校验通过后才保存到会话数据，并在后续回合反馈给 DM Agent。

## 游戏生命周期与场景

### 场景一：游戏初始化（准备阶段）

```
玩家：/dm init
  |
DM Agent → 提出 3-5 个背景选项（赛博朋克、剑与魔法、日式动画等）
  |
[玩家在普通聊天中讨论 —— 对两个 Agent 均不可见]
  |
玩家：/dm choose #1
  |
DM Agent → 确认这次显式选择的选项
  |
DM Agent → 将叙事背景交给 RA
  |
RA → 提出初始化 JSON 补丁：{story_background, major_tasks, genre_rules, ...}
  |
[框架将 allowlisted 补丁应用到 session.world_tags / session.scene]
  |
DM Agent → 要求玩家描述角色
  |
玩家：/dm 我想扮演一个虚弱的老法师
  |
DM Agent → 改写/润色描述，交给 RA
  |
RA → 提出结构化角色卡补丁：
    {name, class, hp, mp, stats, tags, ...}
  |
[框架校验并将 allowlisted 补丁应用到 session.characters]
  |
所有角色准备就绪后 → DM Agent 调用 start_game → 游戏开始
```

**关键模式**：DM Agent 处理所有面向玩家的协商和创意工作。RA 处理所有结构化输出生成。DM 传递叙事；RA 返回 JSON。

### 场景二：游戏周期（活跃游玩）

#### 单玩家行动流程

```
玩家 John：/dm 哪个敌人更容易杀死？
  |
DM Agent → 查询状态，直接回答
  |
[不涉及 RA —— 这是查询，不是行动]

---

玩家 John：/dm 冲锋击杀敌人 B
  |
DM Agent → 调用工具（check_attack_vector, execute_rule, turn_control）
  |
DM Agent → 向玩家叙述结果
  |
行动结果 + 工具轨迹 → 追加到审计缓冲区（audit_buffer，完整数据）
  |
过滤生成 RA 输入投影（ra_cycle_input，不含 player_message，且工具载荷已清洗）
  |
[尚未调用 RA LLM —— 仅数据积累]
```

#### 周期结束流程

```
最后一名玩家行动后：
  |
DM Agent 判断叙事周期已经完成
  |
DM Agent 调用 `cycle_control(action="end_cycle")`
  |
框架触发 RA（仅一次 LLM 调用）
  |
RA 读取：
  - `ra_cycle_input` 投影（本周期过滤后的行动结果：dm_narrative + tools_called）
  - 上一个主状态（权威字段快照）
  - BASE_RULES
  |
RA 产出：
  - 周期摘要 JSON
  - 角色、敌人、场景、规则集的 allowlisted 权威字段补丁候选
  - 对比 DM 叙事与工具/validator 结果的冲突记录
  |
[框架将摘要保存到 session.environment_summaries，并只把验证通过、有工具依据的补丁应用到 session.characters / session.scene]
  |
框架状态机转换：CYCLE_RESOLVING → CYCLE_TRANSITION
  |
框架基于已验证的 RA 摘要生成 DM Agent 的 Cycle Start Prompt
  |
DM Agent 接收：
  - RA 的周期摘要
  - Cycle Start Prompt
  - 完整游戏状态
  |
DM Agent 推进故事 —— "受伤的兽人逃入迷雾中……"
  |
周期 #2 开始
```

**关键模式**：RA LLM 仅在**周期结束时运行一次**，不是每次行动都运行。单个行动结果先缓冲。这控制 Token 成本，并保证玩家体验的低延迟。

### 场景三：游戏结束（战后总结）

```
主线剧情完成 或 DM/admin 显式结束游戏
  |
DM Agent → 提供最终叙事结果
  |
DM 交给 RA → RA 将最终结果结构化为普通周期
  |
框架将完整故事线 + Prompt 交给 DM Agent
  |
DM Agent → 生成战役回顾摘要
  |
RA → 读取所有游戏周期数据 → 产出统计：
  - 每个角色造成的总伤害
  - 击败的敌人数量
  - 关键玩家选择
  - 生存率
  - 使用的规则
  - 剧情分支
  |
[全部保存用于存档 / 回放 / 排行榜]
```

## 周期状态机

一个在现有 `GameMode` 和 `turn_control` 系统之上的高层状态机：

```
CYCLE_ACTIVE（周期活跃）
  |-- 玩家轮流行动（DM 裁决，结果缓冲）
  |-- DM 可通过现有 turn_control 推进战斗回合
  |-- 查询由 DM Agent 直接回答
  |-- DM Agent 调用 `cycle_control(action="end_cycle")`
      |
CYCLE_RESOLVING（周期结算）
  |-- RA 运行（一次 LLM 调用）
  |-- RA 生成摘要和 allowlisted 补丁候选
      |
CYCLE_TRANSITION（周期过渡）
  |-- 框架生成 Cycle Start Prompt
  |-- DM Agent 接收 Prompt + 已验证的状态
  |-- DM Agent 叙述过渡 / 下一幕场景
      |
CYCLE_ACTIVE（下一周期）
```

**注意**：`CYCLE_ACTIVE` 可能跨越多个战斗回合（现有的 `turn_control` 回合）。周期边界是**叙事性**的决策，由 DM Agent 决定，而非战斗机制。

## 数据结构

### 审计缓冲区（audit_buffer）
保存在会话状态中，周期开始时清空，每次行动时追加，用于审计和调试：

```json
{
  "cycle_id": 1,
  "actions": [
    {
      "player_id": "john",
      "character_id": "pc_john",
      "player_message": "冲锋击杀敌人 B",
      "dm_narrative": "John 发起冲锋……",
      "tools_called": [
        {"name": "execute_rule", "args_sanitized": {...}, "result_sanitized": {...}}
      ],
      "timestamp": "..."
    }
  ],
  "started_at": "...",
  "ended_at": "..."
}
```

### RA 输入投影（ra_cycle_input）
框架从 `audit_buffer` 过滤生成的 RA 专用输入，**不包含** `player_message`：

```json
{
  "cycle_id": 1,
  "actions": [
    {
      "dm_narrative": "John 发起冲锋……",
      "tools_called": [
        {"name": "execute_rule", "args": {...}, "result": {...}}
      ]
    }
  ]
}
```

**Allowlist 规则**：RA 输入仅包含 `dm_narrative`、清洗后的 `tools_called`、上一个周期的 `character_status` / `enemy_status` 权威字段、`world_tags`、`rule_sets`、`BASE_RULES`。工具参数和结果必须先投影清洗；任何 PII、诊断字段、原始玩家输入、Prompt 或原始审计载荷均不得传入 RA。

### RA 输出 —— 周期摘要与补丁候选

```json
{
  "cycle_id": 1,
  "summary": "John 重伤了 Orc_B。Alice 治愈了 John。队伍向前推进。",
  "character_status": {
    "pc_john": {"hp": 14, "mp": 8, "conditions": [], "position": [3, 4]}
  },
  "enemy_status": {
    "orc_b": {"hp": 4, "conditions": ["wounded"], "alive": true}
  },
  "world_changes": {
    "new_entities": [],
    "scene_updates": {"current_conflict": "Orc_B 正在撤退"}
  },
  "rules_triggered": ["melee_attack", "heal_light"],
  "dm_narrative_aligned": true,
  "discrepancies": []
}
```

`character_status`、`enemy_status`、`world_changes` 都是补丁候选。框架只有在字段属于 allowlist 且有 tool trace 或 validator 结果支撑时，才会应用这些字段。

### RA 输出 —— 游戏统计（游戏结束）

```json
{
  "total_cycles": 12,
  "characters": {
    "pc_john": {"total_damage_dealt": 147, "enemies_defeated": 3, "times_down": 1}
  },
  "plot_branches": ["spared_the_witch", "burned_the_bridge"],
  "rules_used": {"melee_attack": 24, "fireball": 8},
  "survival_rate": "100%"
}
```

## 流水线顺序（已确认）

**顺序、同步：DM 先执行，然后 RA。**

```
玩家 /dm 消息
  |
DM Agent 运行（多跳工具循环）
  |
DM 响应发送给玩家（玩家立即看到）
  |
行动结果 + 工具轨迹 → audit_buffer（完整）→ 生成 ra_cycle_input（过滤后）
  |
[单个行动尚不调用 RA]
  |
…… 更多玩家行动 ……
  |
DM 调用 `cycle_control(action="end_cycle")`
  |
RA 运行一次（读取 ra_cycle_input + 清洗后的权威字段快照）
  |
框架保存 RA 摘要并应用验证通过的补丁候选
  |
状态机转换
  |
DM Agent 接收下一周期的 Cycle Start Prompt
```

## 关键设计决策

| 决策 | 理由 |
|------|------|
| RA 不直接看到玩家输入 | 防止 RA 对 DM 的玩家意图解读进行二次猜测 |
| RA 每个周期运行一次，不是每次行动 | Token 成本控制；每次行动都调用 LLM 成本过高 |
| 周期是叙事概念，不是战斗概念 | 现有的 `turn_control` 处理战斗回合；周期处理故事节拍 |
| BASE_RULES 作为共享常量 | 两个 Agent 在元机制上达成一致；DM 定义每个战役的细则 |
| 框架保存 RA 摘要并应用验证后的 RA 补丁 | DM Agent 在下一周期通过 System Prompt 读取摘要；权威字段只通过 allowlisted、有工具依据的补丁改变 |
| RA 不覆盖权威结构化字段 | 叙事字段跟随 DM，HP/position/alive 等权威字段以 tool trace 为准；冲突写入 discrepancies |
| audit_buffer 存储完整行动数据，ra_cycle_input 为过滤投影 | RA 需要周期上下文但不得接触 player_message / PII / 诊断字段 / Prompt / 原始审计载荷 |

## 待办事项（设计之后）

1. **投票工具**：未来工作，不在初始范围内。
2. **MemoryCompressor 集成**：可将 RA 摘要作为更高保真度的压缩输入。
3. **RA 使用更便宜的 LLM**：初期使用同一服务商；未来可配置独立模型。
4. **错误处理**：RA 故障不得阻塞游戏，但不得静默丢失结算状态。
   - 无效 JSON / 超时 / 异常：保留未消费 `audit_buffer`，记录 recoverable error 到 `session._ra_recovery_log`
   - 使用 last known authoritative tool state 生成最小状态补丁时，也必须走与 RA 补丁相同的 allowlist/validator 路径
   - `cycle_state` 直接回到 `CYCLE_ACTIVE`（跳过 `CYCLE_TRANSITION`）
   - 下次成功 RA 运行前，不得清空当前周期的 `audit_buffer`

## 涉及文件

| 文件 | 角色 |
|------|------|
| `astrbot_plugin_auto_trpg_dm/main.py` | 插件入口、消息路由、周期状态机钩子 |
| `astrbot_plugin_auto_trpg_dm/core/router.py` | DM Agent 编排、工具循环 |
| `astrbot_plugin_auto_trpg_dm/core/models.py` | GameSession、audit_buffer、ra_cycle_input、RA 输出字段 |
| `astrbot_plugin_auto_trpg_dm/core/prompts.py` | DM System Prompt、RA System Prompt、BASE_RULES |
| `astrbot_plugin_auto_trpg_dm/core/environment_agent.py` | RA 实现（新建） |
| `astrbot_plugin_auto_trpg_dm/tools/registry.py` | 工具白名单（仅 DM；RA 无工具访问权限） |
| `astrbot_plugin_auto_trpg_dm/tools/turn_tools.py` | 战斗回合系统（不变，嵌套在周期内） |

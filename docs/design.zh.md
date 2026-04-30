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
- **可见**：仅 DM Agent 输出 + 工具调用轨迹 + BASE_RULES + 当前会话快照
- **不可见**：玩家原始输入、DM 隐藏备注、System Prompt
- **负责**：结构化数据生成、数值规范化、一致性记录、周期摘要
- **行为**：读取 DM 输出和工具结果，生成结构化 JSON，保存到会话状态
- **输出**：机器可读的结构化 JSON（从不直接发给玩家）
- **不做**：创意决策、叙事生成、工具调用、推翻 DM 叙事

**黄金法则**：RA 严格遵循 DM 叙事。如果 DM 说"地精倒下"但工具轨迹显示还剩 4 HP，RA 记录 DM 叙事的内容，可标注差异，但绝不推翻。

## 共享常量

### BASE_RULES（基础规则）
注入两个 Agent 的 System Prompt 的常量文档，包含：
- 元机制（例如"本游戏使用 d20 骰子系统"）
- 禁止行为（禁止 OOC、禁止元游戏）
- 通用约束（未经同意禁止 PvP、开场后禁止改写剧情）

游戏规则由 DM Agent 在游戏过程中定义，交给 RA 结构化为 `rule_sets`，保存到会话数据，并在后续回合反馈给 DM Agent。

## 游戏生命周期与场景

### 场景一：游戏初始化（准备阶段）

```
玩家：/dm init
  |
DM Agent → 提出 3-5 个背景选项（赛博朋克、剑与魔法、日式动画等）
  |
[玩家在普通聊天中讨论 —— 对两个 Agent 均不可见]
  |
玩家：/dm vote #1
  |
DM Agent → 收集投票、排序、确认最高票
  |
DM Agent → 将叙事背景交给 RA
  |
RA → 结构化为 JSON：{story_background, major_tasks, genre_rules, ...}
  |
[保存到 session.world_tags / session.scene]
  |
DM Agent → 要求玩家描述角色
  |
玩家：/dm 我想扮演一个虚弱的老法师
  |
DM Agent → 改写/润色描述，交给 RA
  |
RA → 规范化为结构化角色卡：
    {name, class, hp, mp, stats, tags, ...}
  |
[保存到 session.characters]
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
行动结果 + 工具轨迹 → 追加到周期缓冲区（Cycle Buffer）
  |
[尚未调用 RA LLM —— 仅数据积累]
```

#### 周期结束流程

```
最后一名玩家行动后：
  |
DM Agent 检测到周期完成（通过回合状态或显式推理）
  |
DM Agent 发出信号："周期完成"
  |
框架触发 RA（仅一次 LLM 调用）
  |
RA 读取：
  - 周期缓冲区（本周期所有行动结果）
  - 工具轨迹
  - 上一个主状态
  |
RA 产出：
  - 更新的主结构化状态
  - 周期摘要 JSON
  - 角色状态表
  - 敌人状态表
  - 世界变更日志
  |
[保存到 session.environment_summaries / session.characters / session.scene]
  |
框架状态机转换：CYCLE_RESOLVED → CYCLE_START
  |
RA 生成 DM Agent 的 Cycle Start Prompt
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
主线剧情完成 或 玩家：/dm end [投票通过]
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
  |-- DM Agent 检测到周期完成 —— 发出信号
      |
CYCLE_RESOLVING（周期结算）
  |-- RA 运行（一次 LLM 调用）
  |-- RA 更新主状态，生成摘要
      |
CYCLE_TRANSITION（周期过渡）
  |-- 框架生成 Cycle Start Prompt
  |-- DM Agent 接收 Prompt + 更新后的状态
  |-- DM Agent 叙述过渡 / 下一幕场景
      |
CYCLE_ACTIVE（下一周期）
```

**注意**：`CYCLE_ACTIVE` 可能跨越多个战斗回合（现有的 `turn_control` 回合）。周期边界是**叙事性**的决策，由 DM Agent 决定，而非战斗机制。

## 数据结构

### 周期缓冲区（Cycle Buffer）
保存在会话状态中，周期开始时清空，每次行动时追加，周期结束时由 RA 消费：

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
        {"name": "execute_rule", "args": {...}, "result": {...}}
      ],
      "timestamp": "..."
    }
  ],
  "started_at": "...",
  "ended_at": "..."
}
```

**重要**：RA **不直接看到** `player_message`。DM Agent 的叙事（`dm_narrative`）和 `tools_called` 才是 RA 的输入。`player_message` 仅用于审计/调试目的存储。

### RA 输出 —— 周期摘要

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
行动结果 + 工具轨迹 → 周期缓冲区（如果是行动）
  |
[单个行动尚不调用 RA]
  |
…… 更多玩家行动 ……
  |
DM 发出周期结束信号
  |
RA 运行一次（读取周期缓冲区 + 状态）
  |
RA 保存结构化输出
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
| RA 输出保存到会话状态 | DM Agent 在下一周期通过 System Prompt 读取 |
| RA 绝不推翻 DM | DM 是创意权威；RA 是书记员 |
| 周期缓冲区存储行动数据 | RA 需要完整周期上下文以生成准确摘要 |

## 待办事项（设计之后）

1. **投票工具**：未来工作，不在初始范围内。
2. **MemoryCompressor 集成**：可将 RA 摘要作为更高保真度的压缩输入。
3. **RA 使用更便宜的 LLM**：初期使用同一服务商；未来可配置独立模型。
4. **错误处理**：RA 故障不得阻塞游戏。无效 JSON → 跳过并继续。

## 涉及文件

| 文件 | 角色 |
|------|------|
| `astrbot_plugin_auto_trpg_dm/main.py` | 插件入口、消息路由、周期状态机钩子 |
| `astrbot_plugin_auto_trpg_dm/core/router.py` | DM Agent 编排、工具循环 |
| `astrbot_plugin_auto_trpg_dm/core/models.py` | GameSession、周期缓冲区、RA 输出字段 |
| `astrbot_plugin_auto_trpg_dm/core/prompts.py` | DM System Prompt、RA System Prompt、BASE_RULES |
| `astrbot_plugin_auto_trpg_dm/core/environment_agent.py` | RA 实现（新建） |
| `astrbot_plugin_auto_trpg_dm/tools/registry.py` | 工具白名单（仅 DM；RA 无工具访问权限） |
| `astrbot_plugin_auto_trpg_dm/tools/turn_tools.py` | 战斗回合系统（不变，嵌套在周期内） |

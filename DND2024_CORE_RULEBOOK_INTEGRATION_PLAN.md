# DND 2024 核心规则库接入 AI DM Agent 方案

本文档用于在新的对话中继续实施：把 `dnd2024_core_rules` 里的中文 CHM 规则资料，整理成适合本项目 AI 驱动 DM 跑团 agent 使用的本地核心规则库。

目标不是把整本规则书塞进模型上下文，而是建立一个本地只读、可按需检索、可追溯来源、上下文开销可控的规则知识层。

## 1. 当前项目背景

项目根目录：

```text
F:\paotuan
```

主要插件目录：

```text
F:\paotuan\astrbot_plugin_auto_trpg_dm
```

CHM 规则资料位置：

```text
F:\paotuan\dnd2024_core_rules\DND.v2026.02.12.chm
```

这个 CHM 是中文 DND 资料合集，内部标题为 `DND五版不全书`。已经确认：

```text
可以用 Windows 自带 hh.exe 反编译。
可以读取 .hhc 目录文件。
.hhc 不是 UTF-8，用 Windows 默认 ANSI/GBK 方向可正常读中文。
目录项约 7926 个。
核心 2024 相关路径包括：
- 玩家手册2024
- 城主指南2024
- 怪物图鉴2025
```

项目当前已有的相关模块：

```text
astrbot_plugin_auto_trpg_dm/core/router.py
astrbot_plugin_auto_trpg_dm/core/prompts.py
astrbot_plugin_auto_trpg_dm/tools/registry.py
astrbot_plugin_auto_trpg_dm/tools/rule_tools.py
astrbot_plugin_auto_trpg_dm/rules/python_runtime.py
astrbot_plugin_auto_trpg_dm/storage/json_repository.py
```

现有能力：

- `IntentRouter` 已有多步工具调用循环。
- `ToolRegistry` 已按游戏模式动态挂载工具。
- `RuleTools` 已有 `register_rule`、`execute_rule`、`list_rules`。
- `PythonRuleRuntime` 适合执行掷骰、伤害、豁免等确定性或随机数值计算。
- `build_system_prompt` 当前已经很长，不适合继续塞大段规则文本。

## 2. 总体目标

为 AI DM agent 增加一个“核心规则知识层”，使 agent 能在需要时查询规则摘要，而不是长期携带所有规则内容。

最终运行时流程应类似：

```text
玩家自然语言行动
  ↓
IntentRouter 判断模式和可用工具
  ↓
必要时调用 query_core_rules 检索少量相关规则卡
  ↓
必要时调用 execute_rule 执行掷骰/命中/豁免/伤害等计算
  ↓
调用 update_scene / update_character_tags / turn_control 写入跑团状态
  ↓
输出简短 DM 裁定和叙事
```

核心原则：

```text
规则知识：query_core_rules 负责
数值结算：execute_rule 负责
跑团状态：现有 session / scene / character / battle 负责
```

不要把规则书内容写入：

```text
session.world_tags
session.scene
session.rules
session.memory_summary
system_prompt
```

规则库应作为静态只读数据存在，不属于任何单个团的动态记忆。

## 3. 非目标

第一阶段不要做这些：

```text
不抽职业全文
不抽子职业全文
不抽背景全文
不抽专长全文
不抽法术详述全文
不抽怪物条目
不抽怪物速查表
不抽魔法物品清单
不抽宝藏表
不抽世界观设定
不抽模组
不抽旧版 2014 内容
不抽第三方内容
不把规则书正文长期塞进 prompt
不把规则库内容写入 session 存档
```

注意版权边界：运行时规则库应保存摘要、流程、关键词、来源路径和必要的短结构化说明，不应生成或分发大段接近原书的完整正文。

## 4. 推荐架构

新增一个独立 rulebook 模块：

```text
astrbot_plugin_auto_trpg_dm/rulebook/
  __init__.py
  models.py
  store.py
  retriever.py
```

新增一个工具模块：

```text
astrbot_plugin_auto_trpg_dm/tools/rulebook_tools.py
```

新增离线构建脚本：

```text
scripts/build_dnd2024_core_rulebook.py
```

或先用 PowerShell 包装：

```text
scripts/build_dnd2024_core_rulebook.ps1
```

运行数据建议放在 AstrBot 插件数据目录下：

```text
data/plugin_data/astrbot_plugin_auto_trpg_dm/rulebooks/dnd2024_core/
```

在本仓库开发环境中，可先输出到：

```text
F:\paotuan\astrbot_plugin_auto_trpg_dm\data\rulebooks\dnd2024_core\
```

但正式插件运行时应使用：

```python
Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_auto_trpg_dm" / "rulebooks" / "dnd2024_core"
```

## 5. 离线构建产物

建议生成：

```text
rulebooks/dnd2024_core/
  manifest.json
  rule_cards.jsonl
  aliases.json
  search_index.json
  source_map.json
  tables.json
  review_warnings.md
```

各文件用途：

```text
manifest.json
  记录构建时间、源 CHM 路径、源 CHM 大小、规则库版本、规则卡数量。

rule_cards.jsonl
  规则卡主数据。每行一个 JSON 对象。

aliases.json
  中文术语、英文术语、玩家常用说法之间的别名表。

search_index.json
  预计算检索索引。第一版可用轻量 n-gram / token 权重。

source_map.json
  rule_id 到原始 CHM 内部路径的映射。

tables.json
  可选。保存少量核心表格，例如状态、武器属性、护甲通用表。

review_warnings.md
  记录抽取异常、空页面、编码问题、疑似表格错列、需要人工复核的规则。
```

## 6. 核心规则抽取范围

第一阶段建议只处理 `玩家手册2024` 的两大核心目录：

```text
玩家手册2024/进行游戏/*
玩家手册2024/术语汇编/*
```

这已经覆盖绝大多数跑团行动裁定：

```text
D20 检定
属性检定
豁免检定
攻击检定
熟练
优势/劣势
动作
反应
附赠动作
移动
躲藏
交涉
探索
战斗流程
攻击
伤害
抗性/易伤/免疫
治疗
临时生命值
生命值降至 0
状态
视野与光照
危害
术语速查
效应区域
其他规则术语
```

第二阶段再补：

```text
玩家手册2024/法术/获得法术.htm
玩家手册2024/法术/施展法术.htm
玩家手册2024/法术/施法时间.htm
玩家手册2024/法术/施法距离.htm
玩家手册2024/法术/法术成分.htm
玩家手册2024/法术/持续时间.htm
玩家手册2024/法术/法术效应.htm
玩家手册2024/法术/法术环阶.htm
玩家手册2024/法术/魔法学派.htm
玩家手册2024/装备/武器.htm
玩家手册2024/装备/护甲.htm
玩家手册2024/装备/工具.htm
玩家手册2024/装备/钱币.htm
玩家手册2024/装备/坐骑与载具.htm
玩家手册2024/装备/制作装备.htm
```

第三阶段再补 DM 裁定工具：

```text
城主指南2024/2.运作游戏/决定掷骰结果/*
城主指南2024/2.运作游戏/运作探索/*
城主指南2024/2.运作游戏/运作战斗/*
城主指南2024/3.地下城主工具箱/危害.htm
城主指南2024/3.地下城主工具箱/陷阱.htm
城主指南2024/3.地下城主工具箱/环境影响.htm
城主指南2024/3.地下城主工具箱/死亡.htm
城主指南2024/3.地下城主工具箱/追逐.htm
```

第四阶段只补怪物数据卡阅读规则，不抽怪物条目：

```text
怪物图鉴2025/前言/数据卡概览.htm
怪物图鉴2025/前言/数据卡的组成部分.htm
怪物图鉴2025/前言/怪物条目.htm
```

## 7. 规则卡数据结构

`rule_cards.jsonl` 每行一个规则卡。建议结构：

```json
{
  "id": "phb2024.core.d20_test",
  "title": "D20检定",
  "category": "core_mechanics",
  "aliases": ["d20检定", "D20 Test", "检定", "判定"],
  "tags": ["d20", "ability_check", "saving_throw", "attack_roll"],
  "summary": "当行动结果不确定时，使用 d20 决定成败。通常掷 1d20，加相关属性调整值、熟练加值和临时修正，与 DC 或 AC 比较。",
  "procedure": [
    "掷 1d20；有优势/劣势时掷两个并取高/低。",
    "加入相关属性调整值、可能的熟练加值和临时修正。",
    "总值大于等于目标数值则成功。"
  ],
  "exceptions": [
    "攻击检定通常比较 AC；属性检定和豁免检定通常比较 DC。"
  ],
  "related_rule_ids": [
    "phb2024.core.advantage_disadvantage",
    "phb2024.core.proficiency",
    "phb2024.combat.attack_roll"
  ],
  "source_refs": [
    {
      "book": "玩家手册2024",
      "path": "玩家手册2024/进行游戏/D20检定.htm"
    }
  ],
  "review_status": "ok"
}
```

字段说明：

```text
id
  稳定英文 ID。不要用中文文件名作为唯一标识。

title
  中文规则名。

category
  大类，用于检索过滤和模式加权。

aliases
  玩家常用说法、英文术语、缩写、误称。

tags
  机器检索标签。

summary
  简短规则摘要，控制在 120-300 中文字符。

procedure
  步骤化裁定流程。

exceptions
  例外、限制、常见误区。

related_rule_ids
  关联规则，帮助工具返回相邻规则。

source_refs
  原始 CHM 内部路径，便于人工核对。

review_status
  ok / needs_review / generated / manual_checked。
```

## 8. 规则分类

建议第一版分类：

```text
core_mechanics
action_economy
combat
damage_healing
conditions
exploration
spellcasting_core
equipment_core
dm_adjudication
monster_statblock_core
```

示例映射：

```text
D20检定 -> core_mechanics
属性检定 -> core_mechanics
豁免检定 -> core_mechanics
攻击检定 -> combat
优势_劣势 -> core_mechanics
动作 -> action_economy
附赠动作 -> action_economy
反应 -> action_economy
战斗流程 -> combat
移动和位置 -> combat
生命值降至0点 -> damage_healing
治疗 -> damage_healing
状态 -> conditions
视野与光照 -> exploration
施展法术 -> spellcasting_core
武器 -> equipment_core
难度等级 -> dm_adjudication
数据卡的组成部分 -> monster_statblock_core
```

## 9. 检索策略

第一版不要急着接向量数据库。核心规则卡数量有限，建议用内存检索：

```text
1. 启动时读取 rule_cards.jsonl。
2. 对 title、aliases、tags、summary、procedure 建轻量索引。
3. 查询时根据标题、别名、分类、标签、中文 n-gram 打分。
4. 返回最高分的 1-5 条。
```

打分建议：

```text
title 精确命中：+100
alias 精确命中：+80
tag 命中：+50
category 命中：+30
query 词出现在 title：+30
query 词出现在 summary/procedure：+10
中文 2-gram / 3-gram 交集：按比例加分
当前模式为 tactical 时，combat/action_economy/conditions/damage_healing 加权
```

别名表非常重要，至少覆盖：

```json
{
  "隐藏": ["躲藏", "Hide", "隐匿", "潜行"],
  "附赠动作": ["Bonus Action", "附动", "bonus"],
  "反应": ["Reaction", "借机攻击", "机会攻击"],
  "倒地": ["Prone", "倒伏"],
  "束缚": ["Restrained", "被束缚"],
  "目盲": ["Blinded", "致盲", "失明"],
  "优势": ["Advantage", "有利"],
  "劣势": ["Disadvantage", "不利"],
  "豁免": ["Saving Throw", "豁免检定", "豁免骰"],
  "命中": ["攻击检定", "attack roll", "命中检定"]
}
```

## 10. 新增工具设计

新增工具：

```text
query_core_rules
```

工具参数模型：

```python
class QueryCoreRulesArgs(BaseModel):
    query: str = Field(..., description="要查询的核心规则问题或玩家行动，例如：倒地状态下攻击如何判定")
    purpose: str = Field(default="adjudication", description="查询目的：adjudication/rules_question/tool_hint")
    categories: list[str] = Field(default_factory=list, description="可选分类过滤，例如 combat、conditions")
    limit: int = Field(default=4, ge=1, le=8, description="最多返回多少条规则卡")
    max_chars: int = Field(default=1600, ge=400, le=3000, description="返回内容最大字符数")
```

工具返回示例：

```json
{
  "ok": true,
  "query": "倒地状态下远程攻击如何判定",
  "matches": [
    {
      "id": "phb2024.conditions.prone",
      "title": "倒地",
      "category": "conditions",
      "score": 0.93,
      "summary": "倒地会影响移动和攻击。近距离攻击者通常更容易命中，远程攻击通常更困难。",
      "procedure": [
        "确认目标是否处于倒地状态。",
        "判断攻击者与目标距离和攻击类型。",
        "根据状态规则给予优势或劣势。"
      ],
      "exceptions": [],
      "related_rule_ids": ["phb2024.combat.attack_roll"],
      "source_refs": [
        {"book": "玩家手册2024", "path": "玩家手册2024/术语汇编/状态.htm"}
      ]
    }
  ],
  "hints": [
    "如果需要实际命中结果，下一步调用 execute_rule 执行 attack_roll。"
  ]
}
```

返回内容必须控制长度：

```text
默认最多 4 条。
默认总字符上限 1600。
每条只返回摘要、流程、例外、来源。
不要返回完整原文。
```

## 11. ToolRegistry 接入点

修改：

```text
astrbot_plugin_auto_trpg_dm/tools/registry.py
```

新增导入：

```python
from .rulebook_tools import QueryCoreRulesArgs, RulebookTools
```

在 `ToolRegistry.for_mode()` 中初始化：

```python
rulebook_tools = RulebookTools(
    repository=self.repository,
    session_id=session_id,
)
```

注册 catalog：

```python
"query_core_rules": make_tool(
    name="query_core_rules",
    description="查询 DND 2024 核心规则摘要，用于动作经济、战斗、状态、伤害治疗、施法通用规则、装备通用规则和 DM 裁定。只返回少量规则卡，不写入跑团状态。",
    model=QueryCoreRulesArgs,
    handler=rulebook_tools.query_core_rules,
),
```

模式挂载建议：

```text
NARRATIVE:
  玩家明确问规则、判定、怎么骰、状态、装备通用规则时挂 query_core_rules。

CHARACTER_CREATION:
  查询装备通用、施法通用、能力检定时挂；不要用它查职业全文。

RULE_AUTHORING:
  挂 query_core_rules，用核心规则辅助生成 execute_rule。

TACTICAL:
  战斗动作、攻击、移动、状态、施法、治疗、伤害、豁免时挂。

RESOLUTION:
  结算、状态持续效果、环境危险、伤害治疗时挂。
```

现有关键词分支里，建议加入：

```text
RULE_QUERY_TERMS
COMBAT_ACTION_TERMS
BATTLE_RESOLUTION_TERMS
RULE_AUTHORING_LIKELY_TERMS
```

注意：工具 schema 本身也占上下文，因此不要在完全无关分支里永远挂载。

## 12. Prompt 接入点

修改：

```text
astrbot_plugin_auto_trpg_dm/core/prompts.py
```

不要加入规则正文。只在硬性规则中增加一条类似：

```text
遇到 DND 2024 核心规则、状态、动作经济、战斗、施法通用规则、装备通用规则或 DM 裁定不确定时，先调用 query_core_rules。
query_core_rules 返回的是只读规则摘要和来源；不要把规则库内容写入 session 状态。
需要掷骰、命中、豁免、伤害、治疗或随机结果时，再调用 execute_rule。
如果 query_core_rules 无命中，说明缺少该规则，不要凭空编造具体书面规则；可以按当前团的裁定风格给出临时裁定并标注为临时裁定。
```

再在工具使用策略中明确：

```text
query_core_rules 用于理解规则。
execute_rule 用于执行数值和随机结果。
update_scene / update_character_tags 用于保存跑团事实。
```

## 13. RuleTools 与 RulebookTools 的分工

不要把规则卡塞进 `register_rule`。

现有 `RuleTools` 用于数值函数：

```text
attack_roll
ability_check
saving_throw
damage_roll
death_save
initiative_roll
concentration_check
healing_roll
contest_check
```

新增 `RulebookTools` 用于规则知识：

```text
D20 检定是什么
倒地状态有什么影响
躲藏需要什么条件
施法成分是什么
伤害抗性如何应用
生命值归零怎么处理
```

推荐预置基础 execute_rule，而不是每次让 LLM 临时注册相似规则。

第一批 seed rules：

```text
d20_check
ability_check
saving_throw
attack_roll
initiative_roll
damage_roll
death_save
concentration_check
healing_roll
contest_check
```

## 14. 离线构建脚本流程

脚本：

```text
scripts/build_dnd2024_core_rulebook.py
```

输入：

```text
F:\paotuan\dnd2024_core_rules\DND.v2026.02.12.chm
```

流程：

```text
1. 使用 hh.exe -decompile 把 CHM 解到临时目录。
2. 读取 DND五版不全书.hhc，编码使用 mbcs 或 gbk 方向。
3. 解析 .hhc 中的 Name、Local、层级。
4. 只保留白名单路径。
5. 读取对应 HTML/HTM 页面。
6. 用 HTML parser 提取 title、h1/h2/h3、p、ul、table。
7. 先生成 raw_page_records.jsonl。
8. 根据页面和标题拆成 rule cards。
9. 加 category、aliases、tags、related_rule_ids。
10. 生成 rule_cards.jsonl、aliases.json、source_map.json。
11. 生成 review_warnings.md。
```

CHM 反编译命令示例：

```powershell
$chm = "F:\paotuan\dnd2024_core_rules\DND.v2026.02.12.chm"
$out = "$env:TEMP\dnd2024_core_chm"
New-Item -ItemType Directory -Force -Path $out
hh.exe -decompile $out $chm
```

读取中文目录时不要用 UTF-8：

```powershell
Get-Content -LiteralPath "$out\DND五版不全书.hhc" -Encoding Default
```

Python 中可优先尝试：

```python
path.read_text(encoding="mbcs")
```

如果在非 Windows 环境中构建，需要额外处理 GBK/GB18030。

## 15. HTML 解析建议

不要用正则硬切正文。建议用标准库或 BeautifulSoup。

如果项目环境不想新增依赖，第一版可以用 Python 标准库：

```python
from html.parser import HTMLParser
```

如果允许开发依赖，BeautifulSoup 更省事：

```python
from bs4 import BeautifulSoup
```

抽取规则：

```text
忽略 script/style/link/meta。
保留 h1/h2/h3。
保留 p 文本。
保留 ul/ol/li 为列表。
表格单独抽成 rows，放到 tables 字段或 review_warnings。
保留 source path。
```

不要把一个 HTML 页面直接作为一个大 rule card。优先按 h2/h3 或术语条目拆分。

## 16. 运行时 RulebookStore 设计

建议接口：

```python
class RulebookStore:
    def __init__(self, rulebook_dir: Path):
        ...

    def load(self) -> None:
        ...

    def query(
        self,
        query: str,
        categories: list[str] | None = None,
        limit: int = 4,
        max_chars: int = 1600,
        mode_hint: str = "",
    ) -> dict[str, Any]:
        ...
```

`RulebookTools` 不应每次重新读文件。可以：

```text
模块级缓存
或 ToolRegistry 初始化时持有 store
或 RulebookTools 内部 lazy-load 单例
```

注意多会话并发：规则库只读，可以共享。

## 17. 上下文预算

目标预算：

```text
query_core_rules 工具 schema：尽量短。
单次 query_core_rules 返回：默认 <= 1600 字符。
规则卡 summary：120-300 中文字符。
procedure：最多 3-6 条。
exceptions：最多 0-4 条。
matches：默认最多 4 条。
```

不要在 system prompt 里列出所有规则分类、所有状态、所有动作。

如果玩家只是问状态列表，可以返回简短列表；如果玩家要完整状态解释，再分批查询。

## 18. 典型运行流程示例

玩家：

```text
我躲到箱子后面，然后射最近的骷髅。
```

理想流程：

```text
1. get_battle_snapshot
   确认当前行动者、位置、掩体、敌人。

2. query_core_rules
   查询：躲藏、攻击、优势/劣势、动作经济。

3. 裁定动作经济
   判断当前回合是否能同时完成躲藏和攻击；必要时要求玩家取舍。

4. check_attack_vector
   检查距离、视线、掩体。

5. execute_rule
   执行 attack_roll 或 ability_check。

6. update_character_tags / update_scene
   写入隐藏、暴露、攻击结果、敌人状态等事实。

7. turn_control record_action
   当前玩家主要行动已结算则推进。

8. 输出
   简短说明画面、裁定、结果、下一步。
```

## 19. 测试用例

实现后至少用这些自然语言测试：

```text
我攻击最近的敌人
我躲藏
我从倒地状态爬起来再攻击
我被束缚还能射箭吗
我想用附赠动作喝药
我生命值归零了怎么办
敌人对火焰有抗性怎么算
我施法被打断了吗
我在黑暗里能看见吗
我想骑马冲锋
我用远程攻击打 5 尺内的敌人
我想趁敌人离开我旁边时砍他
我处于中毒状态还能攻击吗
我治疗倒地队友
我想进行察觉检定
```

预期：

```text
需要规则时会调用 query_core_rules。
需要骰子时会调用 execute_rule。
不会把规则卡写入 session 状态。
不会输出大段规则原文。
不会因为缺少规则卡而编造具体书面规则。
战斗中仍遵守 turn_control 和 spatial 工具事实。
```

## 20. 分阶段实施计划

### 阶段 1：离线目录与页面抽取

交付：

```text
scripts/build_dnd2024_core_rulebook.py
rulebooks/dnd2024_core/raw_page_records.jsonl
rulebooks/dnd2024_core/manifest.json
rulebooks/dnd2024_core/review_warnings.md
```

只处理：

```text
玩家手册2024/进行游戏/*
玩家手册2024/术语汇编/*
```

### 阶段 2：规则卡生成

交付：

```text
rule_cards.jsonl
aliases.json
source_map.json
```

规则卡先可半自动生成，重点保证：

```text
id 稳定
category 正确
summary 简短
source_refs 可追溯
```

### 阶段 3：本地检索

交付：

```text
astrbot_plugin_auto_trpg_dm/rulebook/models.py
astrbot_plugin_auto_trpg_dm/rulebook/store.py
astrbot_plugin_auto_trpg_dm/rulebook/retriever.py
tests/test_rulebook_retriever.py
```

实现内存检索和 max_chars 控制。

### 阶段 4：工具接入

交付：

```text
astrbot_plugin_auto_trpg_dm/tools/rulebook_tools.py
tools/registry.py 中注册 query_core_rules
core/prompts.py 中加入规则查询策略
tests/test_rulebook_tools.py
```

### 阶段 5：预置基础计算规则

交付：

```text
seed rules 或初始化脚本
基础 execute_rule 规则：
- d20_check
- ability_check
- saving_throw
- attack_roll
- initiative_roll
- damage_roll
- death_save
- concentration_check
- healing_roll
- contest_check
```

### 阶段 6：集成测试

交付：

```text
tests/test_rulebook_integration.py
```

重点测试：

```text
工具按模式挂载
query_core_rules 返回长度受控
规则查询不污染 session
战斗行动流程能先查规则再执行规则
```

## 21. 给新对话的起始提示

在新的对话里可以直接这样开场：

```text
请按 F:\paotuan\DND2024_CORE_RULEBOOK_INTEGRATION_PLAN.md 实施第一阶段。
先不要抽全部规则，只处理：
- 玩家手册2024/进行游戏/*
- 玩家手册2024/术语汇编/*

要求：
1. 不修改现有跑团逻辑。
2. 先写离线构建脚本，能从 CHM 反编译并生成 raw_page_records.jsonl、manifest.json、review_warnings.md。
3. 保留来源路径。
4. 不把正文写入 prompt 或 session。
5. 完成后运行相关测试或至少运行构建脚本验证输出。
```

如果要直接做完整接入，可以这样开场：

```text
请按 F:\paotuan\DND2024_CORE_RULEBOOK_INTEGRATION_PLAN.md 实施到 query_core_rules 可用。
第一版只用 JSON 内存检索，不接向量库，不处理职业、法术详述、怪物条目。
请新增 rulebook 模块和 rulebook_tools，并把 query_core_rules 按模式接入 ToolRegistry。
```

## 22. 成功标准

第一版完成后，应满足：

```text
可以从 CHM 生成核心规则卡。
可以用 query_core_rules 查询“倒地、躲藏、优势、攻击、生命值归零”等规则。
每次只返回少量相关规则摘要。
返回包含 source_refs。
规则库不进入 session snapshot。
agent 在战斗/结算时能先查规则，再用 execute_rule 做骰子。
上下文不会因为规则书变大而线性膨胀。
```

## 23. 关键设计判断

最终设计选择：

```text
不用“把规则书直接塞进上下文”。
不用“把规则书塞进 world_tags / memory_summary”。
不用“让 LLM 每次现场注册所有规则”。
不用“一开始就上向量数据库”。

采用：
离线结构化规则卡
本地轻量检索
按需 query_core_rules
确定性 execute_rule
现有 session 状态写入流程
```

这是最符合当前项目架构、上下文成本和跑团实时性的方案。

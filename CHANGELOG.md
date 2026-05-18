# Changelog

所有已经合入 `main` 的用户可见变更都记录在这里。日期使用香港时区对应的开发日期。

## [0.1.122] - 2026-05-18

### Fixed

- 开场后未绑定有效角色的玩家不能继续进行场内行动或检定；需要先建卡或绑定角色，避免“老铂”这类未落库晚加入角色继续污染场景、检定和角色状态。

## [0.1.121] - 2026-05-18

### Fixed

- 根治多人团里未绑定玩家继承 `active_character_id` 的角色错乱：最终回复装备守卫现在只使用当前发言人明确绑定的角色；有 `player_id` 但未绑定时直接跳过，不再把陈大虎、杨永信等当前活跃角色当成“你”。
- 角色卡/晚加入请求会先走角色卡校验链路，避免“我加入游戏，角色名字叫风，弓箭手……”被装备守卫改写成陈大虎装备裁定。
- 收紧“弓”等短词装备持有判定：`弓箭手`、敌方弓手、弓弦等职业/目标描述不再触发当前角色装备拦截，仍保留“你背着弓/端起备用弩/托在手里”等明确未登记持有的拦截。
- 开团后的“加入游戏/角色名字/后继角色”文本会进入角色创建模式并暴露 `create_character` / `bind_player_character`，减少晚加入玩家在叙事路由里空转。

### Verified

- `python -m pytest tests/test_modes.py tests/test_tool_registry.py tests/test_router_usage.py -q`
- `python -m py_compile astrbot_plugin_auto_trpg_dm\core\router.py tests\test_router_usage.py`

## [0.1.120] - 2026-05-18

### Fixed

- 增加最终回复阶段的当前角色装备归属守卫：当 DM 回复把备用弩、军弩、神臂弩、弓或千里眼等写成当前发言人“你”已经持有/使用，但当前角色卡和本轮工具写入都没有确认时，自动替换为裁定修正，避免队友或营地装备被错落到当前角色身上。
- 守卫会区分“询问/申请/尚未确认领用”和“已经拿在手里/试射/扣扳机”等实际持有叙述；允许本轮 `update_character_tags` 明确写入临时领用或借用后再叙述使用。

### Verified

- `python -m pytest tests/test_prompts.py tests/test_router_usage.py -q`
- `python -m py_compile astrbot_plugin_auto_trpg_dm\core\router.py tests\test_router_usage.py`
- `git diff --check`

## [0.1.119] - 2026-05-18

### Fixed

- 修复多人叙事中当前发言人角色锚点不够强，导致队友装备/能力可能被误写成“你”持有的问题；DM 快照现在显式提供 `actor_character`，普通个人状态/装备查询只展开当前角色完整卡，队友仅作为精简 roster 参考。
- 明确 prompt 规则：第二人称“你”和第一人称“我”只指当前发言人绑定角色；回答装备、能力、状态、位置或物品时必须优先以 `actor_character` 及其 tag 为准。

### Verified

- `python -m pytest tests/test_prompts.py tests/test_router_usage.py tests/test_continuity_auditor.py -q`
- `python -m py_compile astrbot_plugin_auto_trpg_dm\core\prompts.py tests\test_prompts.py`

## [0.1.118] - 2026-05-18

### Fixed

- 修复战斗已结束后仍遗留心跳超时暂停锁，导致玩家输入被 `paused_block` 持续拦截的问题；战斗终止/心跳判定终局时会清除旧的自动暂停状态。
- 放宽恢复命令识别，兼容平台把 `resume` 与 `/dm resume` 合并成 `resume/dm resume` 的文本形态。

### Verified

- `python -m pytest tests/test_dm_ack_and_outputs.py tests/test_turn_tools.py tests/test_map_final_sweep.py tests/test_memory_tools.py -q`
- `python -m py_compile astrbot_plugin_auto_trpg_dm\main.py astrbot_plugin_auto_trpg_dm\tools\turn_tools.py tests\test_dm_ack_and_outputs.py tests\test_turn_tools.py tests\test_map_final_sweep.py`

## [0.1.117] - 2026-05-18

### Fixed

- 修复无地图实体时 `ambusher_2` 等内部轮次 ID 泄漏到玩家可见提示的问题；回合状态、心跳超时提示和行动日志现在使用公开标签。
- 修复已被士气溃散、全线溃退等证据结束的战斗无法 `end_encounter` 的问题，并让轮次开启时同步标记外层战斗 active，避免 `battle.active=false` 但 `battle.turn.active=true` 的半战斗状态继续心跳推进。
- 加强时间线推进保护，拦截“明早、明日、亥时、子时”等绕过式隐式推进写入。

### Verified

- `python -m pytest tests/test_turn_tools.py tests/test_memory_tools.py tests/test_map_final_sweep.py tests/test_strict_lifecycle_tools.py tests/test_prompts.py tests/test_router_usage.py -q`
- `python -m py_compile astrbot_plugin_auto_trpg_dm\core\timeline.py astrbot_plugin_auto_trpg_dm\tools\turn_tools.py astrbot_plugin_auto_trpg_dm\main.py tests\test_map_final_sweep.py tests\test_memory_tools.py tests\test_turn_tools.py`

## [0.1.116] - 2026-05-17

### Fixed

- 修复 `/dm` 事件同时被 command handler 和 `on_any_message` 入口处理导致重复回复的问题；两个入口现在按同一个 `message_id` 抢占处理权，第二个入口会静默跳过。
- 新增入口级去重日志 `dm_route_duplicate_suppressed`，用于区分“同一条事件被双入口触发”和玩家真实重复发送。

### Verified

- `python -m pytest tests/test_dm_ack_and_outputs.py tests/test_scenario_templates.py -q`
- `python -m compileall -q astrbot_plugin_auto_trpg_dm tests`

## [0.1.115] - 2026-05-17

### Fixed

- 修复 `/dm` 多行开团正文可能只取到首行的问题；命令入口现在会从 AstrBot 事件的 `message_str`、`message_obj.message_str`、消息链 `message` 和 `raw_message` 中恢复最长的 `/dm` 参数。
- `on_any_message` 入口也复用同一套多行恢复逻辑，避免 command handler 与普通事件入口拿到的文本不一致。

### Verified

- `python -m pytest tests/test_dm_ack_and_outputs.py tests/test_scenario_templates.py -q`
- `python -m compileall -q astrbot_plugin_auto_trpg_dm tests`

## [0.1.114] - 2026-05-17

### Fixed

- 去掉了普通开团路径里的自动模板匹配；除非玩家明确选择预设编号或标题，否则一律把种子交给 LLM 原创编写，不再自动落到低魔边境、底巢或其他模板。
- 一句话开团的偏好追问现在也不再引用模板库标题，而是直接提示 LLM 原创补全，并把 pending 状态标记为 `llm_generated_campaign`。
- 后端默认背景补全、路由兜底和提示词都同步改成 LLM 原创路径，避免“没明确选预设却悄悄套模板”的行为再次出现。

### Verified

- `python -m pytest tests/test_scenario_templates.py tests/test_dm_ack_and_outputs.py -q`
- `python -m compileall -q astrbot_plugin_auto_trpg_dm tests`

## [0.1.113] - 2026-05-17

### Fixed

- 修复结构化自定义剧本文本被“低魔”等弱关键词误判成预设剧本的问题；玩家贴出“时代背景 / 玩家组成 / NPC / 模组限定”等长剧本时，会作为 `player_custom_brief` 原文脚手架保留，不再替换成默认低魔边境背景。
- 预设选择器现在会跳过自定义长剧本，避免“剧情按照这个来搞”中的“这个”被误解成“选这个预设”。
- 开场 prompt 明确区分模板库脚手架与玩家自定义剧本；`source=player_custom_brief` 时必须优先保留原文时代背景、NPC 阵营、模组限定和玩家优势。

### Verified

- `python -m pytest tests/test_scenario_templates.py tests/test_dm_ack_and_outputs.py -q`
- `python -m compileall -q astrbot_plugin_auto_trpg_dm tests`
- `python -m pytest -q`

## [0.1.112] - 2026-05-17

### Added

- 新增开箱即玩的预设剧本入口：未定背景时，玩家可问“有什么预设剧本”，再用编号或标题载入预设；“跑 1 号开始”会直接进入 LLM 开场流程。
- 新增预设剧本库与模板脚手架，包括《底巢清剿：锈蚀圣堂》、雾港悬疑、都市怪谈、星际列车、赛博夜奔、仙门试炼、暖炉酒馆、蒸汽宫廷、废土补给、孤岛遗迹和低魔边境等风格。
- 一句话开团时，如果缺少游戏烈度或玩法取向，开场前只问一次自然语言偏好；玩家回答后会把原始种子、偏好和模板脚手架写入背景，再由 LLM 补齐开场。
- 新增只读 Dashboard 页面和 API，用于查看会话列表、公开快照、审计摘要和备份摘要，避免暴露隐藏真相、密钥或本地路径。

### Fixed

- 无背景提示现在会引导玩家查看预设剧本，而不是只给自定义背景示例。
- 预设选择会清理未完成的开场偏好追问，避免“已选剧本”与旧的 pending 问题互相干扰。

### Verified

- `python -m pytest tests/test_scenario_templates.py tests/test_dm_ack_and_outputs.py tests/test_admin_web.py -q`
- `python -m pytest -q`

## [0.1.111] - 2026-05-16

### Fixed

- 新增 `record_timeline_event` 权威事件时间线工具，用结构化事件记录 C4、爆炸、逃生检定、转场等已发生事实，避免模型只靠旧摘要推断当前状态。
- 新增 `clarify_entity_timeline` 实体澄清工具，把 NPC/角色的当前状态、历史事实、未知项和证据分开写入；可同步修正角色线里的 NPC `known_facts` 和未解钩子。
- Prompt 投影现在包含紧凑的 `event_timeline` 与 `entity_facts`，并明确“旧位置/持物/行动事实跨过爆炸、沉船、检定或转场后必须视为历史事实，不能覆盖较新权威事件”。
- 连续性审计会检查 `event_timeline/entity_facts` 是否被旧 `summary/known_facts` 或 DM 回复降级，并可将有工具证据背书的 NPC 事实修复同步回相关 scene thread。

### Operational Repair

- 已热修 live save：史东状态统一为“已确认生还；当前所在不明”，旧“在中央控制室观察 ROV”事实限定为 C4 爆炸前历史事实，并补齐“逃生路线/当前所在未知”的开放钩子。

### Verified

- `python -m pytest tests/test_memory_tools.py tests/test_tool_registry.py tests/test_prompts.py tests/test_continuity_auditor.py`
- `python -m pytest`

## [0.1.110] - 2026-05-16

### Fixed

- `update_character_tags` 现在和 `update_scene` 一样受高风险行动守卫约束；模型不能在说服、搜索、伤害、生死、搜救等未检定结果前，先把成功事实写进角色状态。
- 完整性守卫现在识别“拒绝玩家事后改写既定事实”的回复，不再在 DM 已明确说明前提不成立时追加“还没有足够骰子支撑成功”的误导尾巴。

### Operational Repair

- 复盘 2026-05-16 日志后修复 live save：音速号已沉没，汉斯、扎古、杰森、淡藤等仍在船上的角色统一标记为下落不明；潜航器三人组状态更新为上浮失败、被沉船碎片缠住且外壳受损。

### Verified

- `python -m pytest tests/test_router_usage.py -q`
- `python -m pytest`

## [0.1.109] - 2026-05-15

### Fixed

- 完整性守卫不再把普通叙事里的“记录坐标/走向某处/藏到控制台下”误判成必须使用战棋空间工具的行动；只有战术地图、攻击、射程、视线、掩体、占位等几何事实才硬性要求空间/回合工具。
- `final_response` 不再盖过同一步里已被守卫拒绝的状态写入；若状态工具被拒绝，路由会让 LLM 先处理守卫结果，而不是直接输出与落库事实矛盾的成功叙事。
- 补充 C4 连续性事故回归测试：非法规则参数、后续成功检定、状态写入、`final_response` 和完整性守卫之间不应再产生“玩家可见成功但系统尾部否认”的错乱。

### Operational Repair

- 已按审计日志中成功检定和玩家可见叙事，人工修复 live save：三块 C4 已在中央控制室控制台下方线缆槽安装并启动 5 分钟倒计时，雅卡不再随身携带 C4。

### Verified

- `python -m pytest tests/test_router_usage.py -q`
- `python -m pytest tests/test_rule_tools.py tests/test_tool_registry.py -q`
- `python -m pytest`

## [0.1.108] - 2026-05-15

### Fixed

- 新增 `resolve_check` 工具作为普通 d20 检定入口，支持搜索、说服、潜行、破解、设备操作和冒险准备等自然语言字段，降低模型必须理解 `execute_rule` 注册规则入参的成本。
- d20 类 `execute_rule` 会归一化 `difficulty`、`target_dc`、`ability_modifier`、`proficiency_bonus` 等常见 LLM 字段，并避免显式 `bonus` 与 `modifier` 被重复计入。
- 状态写入守卫不再被早先失败的规则入参永久卡住；后续成功的 `resolve_check` 或 `execute_rule` 可以清除旧的 `invalid_rule_arguments` 阻塞。
- 系统提示、工具描述和规则检索 hint 现在明确要求普通 d20 行动优先使用 `resolve_check`，自定义规则、伤害、资源消耗等再使用 `execute_rule`。

### Verified

- `python -m pytest tests/test_rule_tools.py tests/test_tool_registry.py tests/test_prompts.py tests/test_router_usage.py -q`
- `python -m pytest -q`

## [0.1.107] - 2026-05-15

### Fixed

- 开场后 `update_world_tags` 会拒绝改写核心世界观字段，例如 `genre`、`tone`、`ruleset`、`world_rules`、`starting_premise` 和 `campaign_background`，避免单句场外设定被写成全团共识。
- 系统提示新增物理环境和设备能力连续性约束；玩家指出水下/干燥、浮力/重力、载具能否飞行或悬停等矛盾时，必须先核对状态并承认修正，不能临时给设备补未记录能力来圆场。

### Verified

- `python -m pytest tests/test_memory_tools.py tests/test_prompts.py tests/test_router_usage.py -q`
- `python -m pytest -q`

## [0.1.106] - 2026-05-15

### Fixed

- 同一群/session 的 LLM 工具循环现在全程串行，不再只在 tactical 模式串行；避免多名玩家的普通叙事请求同时基于旧快照生成、交错写入角色线程和全局场景。
- 同一玩家在上一句相同动作仍在结算时再次发送，会被入口层识别为“进行中重复请求”，不会排队再处理一次，避免同一动作被重复叙事、重复写状态。
- 处理完成、安检拦截、节奏拦截和异常返回都会清理进行中重复标记，避免误挡后续有效动作。

### Verified

- `python -m pytest tests/test_router_usage.py tests/test_dm_ack_and_outputs.py tests/test_long_running_reassurance.py tests/test_cycle_buffer.py tests/test_memory_tools.py -q`

## [0.1.105] - 2026-05-15

### Fixed

- 合并同一角色的 scene thread 别名，统一 `pc_xxx` 与 `character:pc_xxx`，避免同一角色被分裂到两条剧情线后继续沿用已关闭或过期线程。
- 连续性审计现在允许有本轮叙事、工具结果或近期事件支撑的低风险状态标签更新，例如当前位置、当前状态和最近行动；仍会拒绝没有证据的状态改写。
- 已由 `cycle_control` 显式推进过的周期，在后续无 RA 结算时不会再次推进全局时间线，避免一次休息/换日被结算两次。
- 角色名纠错和“查看游戏日志”类请求会进入事实核查提示，优先查询本地记录，而不是被误当成开场后改名或换卡请求拒绝。

### Verified

- `python -m pytest tests/test_continuity_auditor.py tests/test_cycle_buffer.py tests/test_memory_tools.py tests/test_prompts.py -q`

## [0.1.104] - 2026-05-14

### Fixed

- 关键词不再直接改变游戏状态：`update_scene` 只接受显式 `status=closed/resolved/retired/archived` 关闭 scene thread，不再因 summary/current_objective/current_conflict/stakes 里出现“退场/驱逐/无法继续参与”等文字自动关闭线程。
- 周期结算和 RA 摘要不再从普通叙事文本推断“第二天/天亮/入夜”并推进 timeline；只有显式结构化 `timeline`/`time`/`current_time`/`scene_time` 或 `cycle_control(..., timeline_patch=...)` 才能写入全团时间线。
- 玩家输入里的“结局/终幕/幕间/休整”等词不再让本地路由在 LLM 前直接结束战斗回合；只有已落库的结构化场景结束状态会触发本地收束。
- `GameModeStateMachine` 的关键词判断现在只用于本轮工具路由，不再直接把检测出的模式写进存档，避免“地图/角色/攻击”等普通词把会话长期卡进战术或建卡模式。
- 兼容旧 `scene_thread_id` 别名：显式传入已有角色 id 时会归一为 `character:<id>` 并合并旧线程，减少同一角色被分裂到两条 thread 的情况。
- 补齐 `final_response` 工具和可配置的 `llm_tool_loop_max_steps`，让 LLM 在工具事实足够时能显式结束本轮工具循环，默认上限提升到 16 步。

### Verified

- `python -m pytest tests/test_memory_tools.py tests/test_cycle_buffer.py tests/test_cycle_tools.py tests/test_environment_agent.py tests/test_router_usage.py tests/test_tool_registry.py tests/test_modes.py -q`
## [0.1.103] - 2026-05-14

### Fixed

- 角色退场事实改由独立连续性审计 LLM 基于本轮证据判断；本地确定性修复器不再因为“退场/退休”等关键词直接写入退场状态或关闭角色线程。
- 连续性审计器每轮叙事回复后都会运行，能识别死亡、被捕、被驱逐离船、无法继续参与当前故事等非玩家主动声明的退场事实。
- 审计补丁必须带有来自玩家消息、DM 回复、工具结果或存档快照的外部证据；仅靠补丁自己写“已退场”不能自证。
- 同步推进、AFK 默认、后继角色创建和 scene thread 收束现在能识别“被驱逐/无法继续参与/已离开当前故事”等审计器写入的终局状态。
- 已开场跑团不再因为普通消息里出现“角色”二字就切入建卡模式；明确建卡/角色卡/绑定角色请求仍会进入建卡流程。

### Verified

- `python -m pytest tests/test_continuity_auditor.py tests/test_modes.py tests/test_cycle_tools.py tests/test_memory_tools.py -q`
- `python -m pytest tests/test_router_usage.py tests/test_continuity_auditor.py tests/test_modes.py tests/test_cycle_tools.py tests/test_memory_tools.py -q`
- `python -m pytest -q`

## [0.1.102] - 2026-05-14

### Fixed

- 已有退场状态的角色不再因为旧的“当前所在/最近行动”标签或普通工具结果被 scene thread 自动重新打开；只有角色本人明确要求恢复活跃时才允许 reopen。

### Verified

- `python -m pytest tests/test_continuity_auditor.py tests/test_modes.py -q`

## [0.1.101] - 2026-05-14

### Fixed

- 连续性修复器不再把玩家叙述“提前退休后的渔夫生活”“退役经历”“为什么复出”等背景内容误判为当前角色永久退场。
- 显式退场入口仍保留：类似“扎古，退场”“扎古，退休”“算是角色退场了”的玩家明确指令仍会关闭对应角色线程并写入退场状态。

### Verified

- `python -m pytest tests/test_continuity_auditor.py tests/test_modes.py -q`

## [0.1.100] - 2026-05-14

### Fixed

- 连续性修复器不再把“如果要换角色，需要等当前角色退场后……”这类规则说明误判为玩家角色已经退场。
- 连续性审计器不再允许 scene thread patch 用自身的 `status=retired/resolved` 文本自证退场事实；恢复活跃需要玩家明确请求或本轮成功工具结果支撑。
- 已被假退场污染的角色在玩家要求“恢复活跃/仍在参团”时，会清除旧退场 tag 与关闭的角色线程状态。
- `update_scene` 在已有关闭线程收到同一角色新的活跃场景补丁时，会重新打开该线程，避免后续剧情继续写进 `retired/resolved` 线程。
- Prompt projection 会保留当前发言玩家自己的并行 scene thread，减少多人并行时 DM 丢掉该玩家当前位置、线索或剧情焦点。
- 已开场跑团内补充 NPC/船员名单不再被误判为新团开局并触发重开确认。

### Verified

- `python -m pytest -q`

## [0.1.99] - 2026-05-14

### Fixed

- `/dm` 多行新团种子会优先使用原始事件里的完整命令参数，避免 AstrBot `GreedyStr` 在换行后只把第一段传给 DM。
- 出站封闭选项清理器只在游戏已经开场后的正式行动回合启用，不再删除开局、世界设定或建卡阶段的“建议角色方向”等准备内容。
- 开局背景、三幕大纲、起始前提、势力文本、开局回复和 prompt projection 的压缩上限放大，避免长剧本种子存下来了却在下一轮只暴露前半段给 LLM。

### Verified

- `pytest -q`

## [0.1.94] - 2026-05-13

### Fixed

- `turn_control start_round/start_scene_resolution` 在没有战棋地图实体、也没有显式 `turn_order` 时不再默认把全团已绑定角色拉进战斗轮次，避免异地或已睡觉玩家被战斗心跳超时卡住全局时间。
- 战斗状态查询会把仍在进行的 `battle.turn.active` 视为 active，避免底层回合心跳仍在跑时对玩家回答“没有战斗”；已暂停或结束的回合不再强制进入战术模式。

### Verified

- `python -m pytest tests/test_turn_tools.py tests/test_spatial_tools.py tests/test_modes.py -q`
- `python -m pytest -q`

## [0.1.93] - 2026-05-13

### Fixed

- 角色状态摘要在同层标签过多时优先保留最近写入的 status/relations/notes，避免旧待办或旧失败记录挤掉新近猎获、鱼获、物资和赠与事实。
- 更新已有角色 Tag 时会刷新其最近顺序，使修正后的事实进入下一轮 prompt 的可见摘要。
- `session_control(debug_last)` 背后的审计读取会跨当前 `.jsonl` 和轮转备份 `.jsonl.1/.2/.3` 取最近记录，玩家要求核对前文时不再只看到当前小尾巴。
- 玩家指出“记错、漏算、去哪了、还我、检索/核对/修正剧情”时进入事实核查提示，要求先查审计/记忆工具，再否认物品、猎获、鱼获、线索或赠与；包含“日志”的事实修正请求不再被误路由成只读诊断。

### Verified

- `python -m pytest tests/test_models.py tests/test_json_repository.py tests/test_prompts.py tests/test_router_usage.py::test_diagnostic_request_excludes_fact_check_corrections -q`
- `python -m pytest -q`

## [0.1.91] - 2026-05-09

### Added

- 已开场跑团新增可持续维护的公开进展结构：`scene.current_objective`、`open_hooks`、`clues`、`mysteries`、`stakes` 和 `pressure_clock`，用于保存当前目标、开放钩子、可见线索、未解问题、利害关系和压力变化。
- 新增“当前目标/线索/任务”只读状态查询路径，返回简明公开状态，不推进剧情。
- `start_game` 现在要求明确 `initial_hook`，并在开场后自动补齐首个 objective、至少两个 open hooks、stakes / pressure 结构。

### Changed

- DM prompt 强化普通叙事回复的可行动信息：默认包含当前可感知事实、一个正在变化的压力、至少一个可交互线索/对象/地点/NPC 动机，同时继续禁止普通回复生成封闭式 `1/2/3` 行动菜单。
- 调查、询问、搜索、交易、交涉或战斗后，DM 会被明确引导用 `update_scene` 维护 clue/open hook/mystery/objective/stakes/pressure 状态；线索状态保持 `discovered`、`suspected`、`resolved`、`false_lead`、`blocked` 等简单值。
- NPC/阵营关系状态投影只暴露玩家已能感知或已知的态度、恐惧、债务、已知事实和最近互动，隐藏动机、真实阵营和未来背叛计划不进入普通 DM prompt。

### Fixed

- Prompt projection、工具结果投影和目标/线索状态查询会过滤 `hidden`、`secret`、`dm`、`dm_only`、`gm_only` 等隐藏记录，避免幕后真相、未发现地点或 DM-only NPC 泄漏到玩家侧输出。
- 修复 Hermes Coder 在 AstrBot 兼容层里的导入兼容问题。
- 修复当前 Python 3.8 测试环境下 strict-grid SVG renderer 测试类型注解和长耗时安抚测试事件循环兼容问题。

### Verified

- `python -m compileall -q astrbot_plugin_auto_trpg_dm astrbot_plugin_hermes_coder tests scripts`
- `python -m pytest -q`
- `git diff --check`

## [0.1.90] - 2026-05-07

### Changed

- 地图交付 Phase 3 迁移收口：普通 strict / tactical / overview 地图请求优先使用确定性 renderer，legacy `generate_map_svg` 保留为显式 fallback、风格实验或迁移兼容路径。
- README 补充交付节奏、legacy SVG 降级策略、旧存档兼容和相关目录/文档入口，避免把 SVG / PNG 或本地 artifact 路径当成权威地图事实。

### Fixed

- 保持 `v0.1.90` 代码版本、插件 metadata、README 和 changelog 版本说明一致。

### Verified

- `python -m compileall -q astrbot_plugin_auto_trpg_dm tests scripts`
- `python -m pytest tests\test_hermes_coder_bridge.py tests\test_hermes_coder_plugin.py tests\test_hermes_notify_cron_output.py`
- `git diff --check`
## [0.1.89] - 2026-05-04

### Added

- 氛围图片支持在插件配置里直接填写 `ambient_image_api_key`；留空时仍回退到 `ambient_image_api_key_env` 指向的环境变量。
- 新增普通玩家可用的手动氛围图触发路径，仍会走频率、相似度、下载安全和独立发送流程。
- 氛围图片 provider 请求支持配置浏览器风格 `User-Agent`，用于兼容会拒绝默认 Python UA 的图片 API 网关。
- 为 AstrBot 插件市场发布补齐 `requirements.txt`、`metadata.yaml` 仓库信息、展示名、短描述、平台声明和最低 AstrBot 版本。
- 为随包 DND / SRD 摘要规则卡补充 README 版权边界说明和 SRD 5.2 attribution。
- 新增标准 MIT `LICENSE` 和 `NOTICE`，明确代码许可、SRD 5.2 归因、规则卡边界和商标关系。

### Changed

- 重写 README，使安装、配置、隐私边界、依赖和稳定版仓库拆分路径更适合插件市场用户阅读。

### Verified

- `python -m compileall -q astrbot_plugin_auto_trpg_dm tests scripts`
- `python -m pytest -q`

## [0.1.87] - 2026-05-03

### Fixed

- NAS 部署脚本在归档并替换旧插件目录前会先尽量移除 `__pycache__`，并把旧目录清理失败降级为 warning，避免容器内 root 生成的 `.pyc` 权限阻断文件部署。

## [0.1.86] - 2026-05-03

### Added

- 新增 DM 出站回复清理层，默认移除普通叙事回复末尾的编号行动菜单、隐藏式问句菜单、目标选择菜单和线索聚焦菜单。
- 增加 `docs/dm-outbound-cleanup.md`，说明清理边界、显式求助行为、审计隐私和语义复核限制。
- 长时间 `/dm` 裁定前会发送轻量确认回复，并把本轮多个骰子 pending 输出合并成一段检定摘要。

### Changed

- 更新 DM prompt，强调普通回复应给出场景、可感知细节、压力、风险、后果和必要澄清，而不是生成下一步行动菜单。
- 显式求助时可以保留玩家主动要求的可见目标列表，但普通建议菜单会被软化或移除。
- 规则注册失败时为常见生成规则错误提供更友好的 `register_rule` 提示。

### Fixed

- 加强 Python 规则校验，注册前拒绝 helper 名称覆盖和未定义名称，避免错误规则进入运行时。
- 出站清理审计只记录 hash、位置、长度、分类和本地原因，不保存被移除的候选文本或语义复核原始理由。

## [0.1.85] - 2026-05-03

### Fixed

- NAS 部署脚本改为先上传 LF 格式的临时 shell 脚本再执行，避开 PowerShell 管道向 SSH stdin 写入 CRLF 的问题，并在远端执行后清理临时脚本。

## [0.1.84] - 2026-05-03

### Fixed

- NAS 部署脚本在通过 stdin 发送远端 shell 脚本前会统一转换为 LF 换行，避免 Windows CRLF 的 `\r` 混入远端路径或命令参数。

## [0.1.83] - 2026-05-03

### Fixed

- NAS 部署脚本改为通过 stdin 向远端 `sh -s` 传递多行脚本，避免 Windows OpenSSH 转发命令参数时破坏双引号，导致 `restartCommand` 被拆成空参数。

## [0.1.82] - 2026-05-03

### Fixed

- 修复 Synology `/bin/sh` 对部署脚本中 `sh -lc` 兼容性不稳定时，`restartCommand` 参数可能被丢弃并只打印 Docker 帮助的问题；现在改用更保守的 `sh -c` 执行远端重启命令。

## [0.1.81] - 2026-05-03

### Changed

- NAS 部署脚本会在替换插件目录后立即验证远端 `metadata.yaml` 版本，方便区分“文件已更新”和“容器已重启”。
- NAS 部署脚本的 `restartCommand` 增加硬超时，默认 120 秒；可通过本地配置 `restartTimeoutSeconds` 或命令行 `-RestartTimeoutSeconds` 调整，避免 Docker / Container Manager 卡住时部署命令无限等待。

### Fixed

- 改进 README 的 NAS 部署说明，补充 Docker 容器管理通道卡住时的处理边界：文件部署成功不等于运行中 AstrBot 已重新加载。

## [0.1.80] - 2026-05-03

### Added

- 新增可选 TRPG 氛围图片生成，默认关闭；支持 Images API 与 Chat Completions 两种 provider 模式。
- 增加活跃窗口门禁、最少消息数、最少玩家数和相似 prompt 重试/跳过，避免普通剧情里过度生图。
- 图片生成改为后台任务，成功后独立发送 `{title}` 和图片，不阻塞普通 `/dm` 回复。
- 增加 [docs/ambient-image-api.md](docs/ambient-image-api.md)，说明配置、触发规则、隐私边界、费用风险和排错方式。

### Fixed

- 为 provider 返回的图片 URL 加上 localhost、私网、link-local、reserved、multicast 和 DNS 解析结果拦截。
- 为 provider 响应、URL 下载和 `b64_json` 图片加上大小上限，并校验下载 content type。
- 禁止图片下载自动跟随未校验 redirect，防止 provider URL 通过跳转绕过 SSRF 检查。
- 修复氛围图片 provider 在 Python 3.8 环境下的类型别名和线程执行兼容问题。

### Verified

- `python -m compileall -q astrbot_plugin_auto_trpg_dm tests scripts`
- `python -m pytest -q`
- `git diff --check`

## [0.1.79] - 2026-05-03

### Fixed

- 修复 `LocalFunctionTool` 在部分 AstrBot 版本或测试替身中缺少 `validate_parameters()` 时导致工具注册失败的问题。
- 兼容部分 AstrBot `ToolSet` 测试替身不接受构造参数的情况，避免工具集合创建失败。
- 为 Honcho 外置记忆调用补上 Python 3.8 兼容线程执行路径，避免旧运行环境缺少 `asyncio.to_thread()`。
- 恢复 GitHub Actions 中 `tests/test_tool_registry.py` 对普通请求和 token 诊断请求工具裁剪逻辑的验证能力。

### Verified

- `python -m compileall -q astrbot_plugin_auto_trpg_dm tests scripts`
- `python -m pytest -q`
- `git diff --check`

## [0.1.78] - 2026-05-02

### Added

- 新增 Recorder Agent，简称 RA，的模型配置项：`ra_model_provider` 与 `ra_max_tokens`。
- 增加 RA 周期结算相关集成测试，覆盖可选启用、模型 provider 选择和输出约束。
- 在 README 中补齐当前架构、部署、隐私边界、PR 流程和测试说明。

### Changed

- 优化开局/建团引导，减少玩家已经开团后继续被要求补充背景资料的情况。
- 收紧开局后的资源裁定，避免 agent 在玩家未明确拥有资源时过度慷慨地补发物品或能力。
- 降低重复规则列表对 prompt 的占用，让规则上下文更集中在当前裁定需要的内容。
- 明确 prompt 组件 token 指标，方便后续继续压缩上下文成本。
- 强化 TRPG 平衡守卫，减少越权状态修改、规则编造和不必要的玩家控制。

### Fixed

- 修复部分 AstrBot / LLM provider 不支持透传 `max_tokens` 时 RA 调用失败的问题；现在会自动重试一次不带 `max_tokens` 的调用。
- 修复动态规则执行中的若干失败路径。
- 修复 #5 合并后 `git diff --check` 检查暴露的尾随空白问题。

### Verified

- `python -m compileall -q astrbot_plugin_auto_trpg_dm tests scripts`
- `git diff --check`
- 发布前隐私扫描，确认 NAS 配置、SSH 私钥、真实 API key 和运行数据没有进入 diff。

## [0.1.74 - 0.1.77] - 2026-05-01

### Added

- 接入可选 Honcho 外置记忆增强，支持受控摘要、关键事件同步、prompt context 注入和失败降级。
- 支持 Honcho Cloud 与自托管 Honcho，增加 `auto`、`cloud`、`self_hosted` 目标选择。
- 增加 Honcho 外置记忆的配置文档、控制项说明和排错指南。
- 新增轻量 PR 检查脚本 `scripts/handle-pr.ps1`，用于拉取、检查、合并和可选部署 PR。
- 新增 NAS 部署脚本 `scripts/deploy-nas.ps1` 与示例配置，支持本地打包、NAS 备份、替换和容器重启。
- 新增 GitHub Actions PR / push 检查。

### Changed

- Honcho 外置记忆默认关闭，并与本地 JSON 权威存档保持分层。
- 自托管 Honcho 支持无鉴权本地 Docker 环境，也支持通过环境变量启用 API key。
- NAS 部署脚本改为只部署当前 Git `HEAD`，并在工作区不干净时拒绝部署。

### Fixed

- 修复 NAS 部署命令转发问题。
- 加固 NAS 部署前的 pytest 检测：本地没有安装 `pytest` 时只跳过 pytest，不影响 `compileall`。
- 在 NAS 上使用 legacy SCP 模式，提高 Synology 环境兼容性。

## [0.1.70 - 0.1.73] - 2026-04-30

### Added

- 接入 DND 2024 本地核心规则书检索层。
- 新增 `query_core_rules`，用于查询规则摘要、动作经济、状态、施法通用规则、装备通用规则和 DM guidance。
- 增加规则书工具、DM guidance、安全策略和路由行为相关测试。

### Changed

- 将规则知识与数值执行进一步分离：规则摘要走本地检索，骰子和数值结算走本地规则运行时。
- 优化 Intent Router 的工具挂载，让不同模式只拿到当前需要的工具集合。
- 减少把大段规则文本长期挂入 prompt 或 session 的情况。

## [0.1.18] - 2026-04-28

### Added

- 初始化 AstrBot Auto TRPG DM 插件结构。
- 增加自然语言 `/dm` 入口、Intent Router、基础状态模型、本地 JSON 存档和测试骨架。
- 增加基础战棋空间、规则运行时、角色/场景记忆工具和开发文档。

# Changelog

所有已经合入 `main` 的用户可见变更都记录在这里。日期使用香港时区对应的开发日期。

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

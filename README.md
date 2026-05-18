# AstrBot Auto TRPG DM

全自然语言 TRPG DM 插件，基于 AstrBot v4.5.7+。当前插件版本：`v0.1.120`。

这个插件把 AstrBot 变成一个可长期跑团的小型 TRPG runtime。玩家只需要像聊天一样说“我靠墙潜行过去，再射最近的敌人”，插件会结合当前场景、角色状态、战棋事实、本地规则和 LLM 裁定完成回应。

[![PR checks](https://github.com/unhumanplane/paotuan/actions/workflows/pr-check.yml/badge.svg)](https://github.com/unhumanplane/paotuan/actions/workflows/pr-check.yml)

## 适合什么场景

- 纯文字跑团，希望玩家直接描述行动、追问和推进剧情。
- 带轻量战棋或位置概念的团，需要明确移动、视线、距离、掩体和轮次。
- 想让 AI 做 DM / 协同 DM，但仍希望关键事实、数值和规则执行有本地约束。
- 需要长期存档、审计、恢复和可持续维护，而不是一次性 demo。

它不是“全自动完美 DM”。更准确地说，它是一套把自然语言入口、状态机、工具调用、战棋事实、本地规则和可选外置记忆组合起来的工程骨架。LLM 负责理解与叙事，本地工具负责保存事实、执行规则和维护状态。

## 主要能力

### 自然语言入口

- 默认使用 `/dm` 作为显式入口，避免普通群聊被误接入 LLM。
- 支持 `/DM`、`/Dm`、`/dM` 等大小写误用。
- 玩家不需要记忆 `/move`、`/attack`、`/roll` 这类命令，Intent Router 会按场景选择工具。
- 普通 DM 回复会抑制“1/2/3 选项”“还是 A/B/C”“下一步菜单”这类行动菜单，让玩家直接描述想尝试的行动；设计边界见 [docs/dm-outbound-cleanup.md](docs/dm-outbound-cleanup.md)。
- 当一次 bot-handled `/dm` 请求超过配置阈值仍未完成时，可以发送一条带明确前缀的短等待提示；配置和边界见 [docs/long-running-reassurance.md](docs/long-running-reassurance.md)。

### 跑团状态与角色

- 创建、绑定和维护角色卡。
- 用 Tag 型结构保存角色、装备、风格、默认战斗行为和世界设定。
- 维护场景摘要、长期剧情钩子、最近事件、会话备份和恢复数据。
- 支持多人团里的控制权约束，减少旁观者误操作。

### 战棋与轮次

- 内置网格空间引擎，用于坐标、移动、障碍、视线、掩体和距离校验。
- 支持地图实体、回合顺序、行动推进和结算阶段。
- 回合超时后可以执行保守自动行动，避免多人团长期卡住。
- SVG / PNG 地图只作为视觉展示，不直接改写战棋事实。
- 后续玩家地图渲染必须从结构化坐标、边界、锚点、路径点和连接记录生成，不能把 SVG / PNG 或模糊空间短语当成权威地图事实；坐标与布局合同见 [docs/coordinate-renderer-contract.md](docs/coordinate-renderer-contract.md)。
- MapCore 负责地图记录、可见性投影和候选地图事件校验，防止 DM / RA / LLM 读取隐藏地图事实或 raw 地图存储；设计边界见 [docs/mapcore-projection-guard.md](docs/mapcore-projection-guard.md)。
- Phase 3 地图交付迁移已收口：普通地图请求优先走确定性 strict-grid / overview-topology renderer，legacy `generate_map_svg` 仅作为显式 fallback、风格实验或迁移兼容路径；交付节奏、旧存档兼容和本地路径隐藏策略见 [docs/delivery-cadence-legacy-svg-migration-prd.md](docs/delivery-cadence-legacy-svg-migration-prd.md)。显式可视化地图请求会被 code guard 要求至少尝试确定性 renderer，不能被 ASCII / 表格 / 文字地图静默替代；行为边界见 [docs/map-request-hard-routing.md](docs/map-request-hard-routing.md)。

### 规则与裁定

- 内置受限 Python 规则运行时，可注册和执行本地规则函数。
- `resolve_check` 负责搜索、说服、潜行、破解、操作设备等普通 d20 检定；`execute_rule` 负责命中、豁免、伤害、治疗、资源消耗和已注册规则等数值结算。
- `query_core_rules` 用于查询随插件发布的 DND 2024 规则摘要和 DM guidance。
- 规则书内容按需检索，不会把整本规则长期塞进 prompt 或存档。

### 可选 Honcho 外置记忆

Honcho 是可选增强层，默认关闭。它不替代本地 JSON 存档，只用于辅助回忆玩家偏好、角色倾向、伏笔、幕间 recap 和关键事件。

支持模式：

- `auto`：有自托管地址时使用 self-hosted，否则使用 cloud。
- `cloud`：强制使用 Honcho Cloud。
- `self_hosted`：强制使用自托管 / Docker Honcho。

完整说明见 [docs/honcho-external-memory.md](docs/honcho-external-memory.md)。

### 可选 Recorder Agent

Recorder Agent，简称 RA，是可选的周期结算/记录 agent，默认关闭。它用于在主 DM 流程之外做更稳的阶段性整理，并支持独立模型 provider 和 token 上限建议。

相关配置：

- `ra_enabled`
- `ra_model_provider`
- `ra_max_tokens`

如果底层 LLM provider 不支持透传 `max_tokens`，插件会自动重试一次不带 `max_tokens` 的调用，避免因为 provider 兼容性导致整轮流程失败。

### 独立连续性审计

连续性审计器默认启用。它只在高风险轮次触发，例如玩家指出事实丢失、角色退场、场景状态被工具更新、或 DM 回复疑似否认已发生事实时，使用独立上下文再跑一次轻量 LLM 检查。审计器只接收精简存档、本轮工具轨迹、玩家消息和 DM 回复，不继承主 DM 的长 prompt；框架只自动应用白名单内的安全修补，例如关闭退场线程、把误切的全局建卡模式拉回叙事模式、规范 active scene thread。无法安全自动修的内容只写入审计记录。

相关配置：

- `continuity_auditor_enabled`
- `continuity_auditor_model_provider`
- `continuity_auditor_max_tokens`

### 可选氛围图片

氛围图片是可选视觉辅助，默认关闭，不接受玩家直接命令生图。它和 SVG 战棋地图是两套功能：SVG 地图用于位置、距离、视线和战场示意；氛围图只用于渲染剧情气氛、帮助玩家理解关键场景，不会写入任何权威游戏事实。

当前接入目标是 PackyAPI `gpt-image-2`，默认走 `/v1/images/generations`，也可以切换到 `/v1/chat/completions`。图片 API key、base URL、模型、尺寸、质量、返回格式、触发频率、活跃度门禁、prompt 语义去重和 prompt 模板都通过 AstrBot 插件配置设置。生成完成后会单独发送标题和图片，不附着到普通 `/dm` 回复上。

安全边界：

- 下载 provider 返回的 URL 前会阻止 localhost、私网、link-local、reserved、multicast 等地址。
- provider 响应、URL 下载和 `b64_json` 图片都有大小上限。
- 图片下载会校验 content type。
- 普通 `/dm` 回复不会等待图片生成，图片生成在后台任务中独立完成。

完整配置、触发规则、隐私边界和费用风险见 [docs/ambient-image-api.md](docs/ambient-image-api.md)。

## 安装

### 从插件市场安装

插件上架后，推荐直接在 AstrBot 插件市场搜索 `Auto TRPG DM` 或 `auto_trpg_dm` 安装。

### 从 Git 仓库安装

如果手动安装，请把插件目录放入 AstrBot 的插件目录：

```text
astrbot_plugin_auto_trpg_dm/
  main.py                 # AstrBot 插件入口与 /dm 事件处理
  metadata.yaml
  _conf_schema.json
  core/
    router.py             # Intent Router，多步工具调用与模式切换
    models.py             # GameSession、角色、战斗和周期状态模型
    map_core.py           # MapCore store、角色投影和候选地图事件校验
    prompts.py            # 系统提示与模式提示
    security.py           # 输入安全预检查
    external_memory.py    # Honcho 外置记忆适配
    ambient_image.py      # 氛围图片 provider、安全校验和物化逻辑
  tools/
    registry.py           # 按模式挂载工具
    memory_tools.py       # 角色、场景、世界设定和存档工具
    spatial_tools.py      # 战棋空间工具
    turn_tools.py         # 轮次、超时和行动推进
    rule_tools.py         # 本地规则执行
    rulebook_tools.py     # DND 2024 / DM guidance 检索
    map_tools.py          # 视觉地图生成
    ambient_image_tools.py # 氛围图片触发、prompt 和元数据保存
  rules/
    python_runtime.py     # 受限 Python 规则运行时
    dice.py               # 骰子工具
  rulebook/
    store.py              # 本地规则卡存储
    retriever.py          # 规则检索
  spatial/
    grid.py
    engine.py
    los.py
  storage/
    json_repository.py    # 本地 JSON 存档
tests/
scripts/
docs/
```

插件依赖会由 `astrbot_plugin_auto_trpg_dm/requirements.txt` 声明。当前必需依赖是：

```text
pydantic>=1.8,<3
pillow>=10.0.0
```

Honcho 是可选增强能力，默认关闭。只有在你主动开启 `honcho_enabled=true` 时，才需要额外安装 Honcho Python SDK。

```bash
pip install honcho
```

核心思路是把“事实”和“叙事”分开：

- 坐标、视线、距离、掩体、数值结算由本地工具负责。
- LLM 负责理解玩家自然语言、组织裁定、调用工具和输出叙事。
- DM / RA / 玩家侧只能消费 code 投影后的地图视图，不能读取 raw map store 或隐藏地图事实。
- 存档、规则执行和审计结果写回本地 JSON，避免只存在上下文里。

关系系统同样走轻量 JSON 状态，而不是数值攻略条。NPC 和阵营可以记录 `attitude`、`trust`、`fear`、`debt`、`leverage`、`known_facts`、`last_interaction`、`flags` 等可解释字段，用来让 DM 在后续线索、价格、协助和敌意裁定里保持一致。玩家能查询到的是角色可感知或已知的部分；隐藏动机、秘密效忠和未揭露背叛不会投影到普通 prompt 或玩家回复里。Honcho 如启用只作为偏好和伏笔增强，关系事实仍以本地 JSON 存档和工具轨迹为准。

## 快速开始

默认情况下，只需要在聊天里使用 `/dm`：

```text
/dm 我想开一个黑暗奇幻调查团，角色是一个谨慎的游荡者。
```

建议首次使用时保持：

- `allow_private_chat=false`
- `honcho_enabled=false`
- `ra_enabled=false`
- `ambient_image_enabled=false`

等基础流程稳定后，再逐项打开外置记忆、RA 或氛围图片。

## 常用配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled_sessions` | 空列表 | 允许自动接管的会话 ID。通常留空，只用 `/dm`。 |
| `trigger_prefixes` | `/dm` | 自定义触发词。 |
| `allow_private_chat` | `false` | 是否允许私聊不带 `/dm` 也触发。建议关闭。 |
| `prompt_snapshot_projection_enabled` | `true` | 是否减少 prompt 中的冗余存档字段。 |
| `heartbeat_idle_log_interval` | `10` | 轮次心跳空闲日志采样间隔。 |
| `honcho_enabled` | `false` | 是否启用 Honcho 外置记忆。 |
| `honcho_target` | `auto` | `auto` / `cloud` / `self_hosted`。 |
| `honcho_timeout_seconds` | `8` | Honcho 单轮读写超时，失败会降级。 |
| `honcho_cross_campaign_personalization_enabled` | `false` | 是否允许跨团复用玩家偏好。 |
| `ra_enabled` | `false` | 是否启用 Recorder Agent。 |
| `ra_model_provider` | `default` | RA 使用的模型 provider。 |
| `ra_max_tokens` | `2048` | RA 输出 token 上限建议值。 |
| `continuity_auditor_enabled` | `true` | 是否启用独立上下文连续性审计器。 |
| `continuity_auditor_model_provider` | `default` | 连续性审计器使用的模型 provider。 |
| `continuity_auditor_max_tokens` | `1200` | 连续性审计器输出 token 上限建议值。 |
| `llm_tool_loop_max_steps` | `16` | 单次 `/dm` 请求内最多允许的 LLM 工具循环步数。 |
| `ambient_image_enabled` | `false` | 是否启用 TRPG 氛围图片。 |
| `ambient_image_api_mode` | `images` | 图片 API 路径：`images` 或 `chat_completions`。 |
| `ambient_image_base_url` | `https://www.packyapi.com` | 图片 API base URL。 |
| `ambient_image_api_key` | 空 | 可直接填写图片 API key；留空时读取环境变量。 |
| `ambient_image_api_key_env` | `PACKYAPI_SORA_API_KEY` | 图片 API key 所在环境变量名。 |
| `ambient_image_user_agent` | 空 | 生图 API 请求的 User-Agent；留空时使用内置浏览器风格 UA。 |
| `ambient_image_frequency` | `medium` | 普通氛围图触发频率。 |
| `ambient_image_activity_window_minutes` | `60` | 普通氛围图活跃窗口。 |
| `ambient_image_activity_min_messages` | `10` | 活跃窗口内最少玩家消息数。 |
| `ambient_image_activity_min_players` | `2` | 活跃窗口内最少不同玩家数。 |

完整配置项见 [astrbot_plugin_auto_trpg_dm/_conf_schema.json](astrbot_plugin_auto_trpg_dm/_conf_schema.json)。

## 数据与隐私

运行数据会写入 AstrBot 数据目录，例如：

```text
data/plugin_data/astrbot_plugin_auto_trpg_dm/
  saves/
  rules/
  audit/
  maps/
  ambient_images/
  rulebooks/
  logs/
```

这些数据不应该提交到 Git。它们可能包含跑团记录、玩家发言摘要、地图输出和审计信息。

插件默认不会把 Honcho、RA、氛围图片等增强能力打开。涉及外部服务的功能都需要你显式启用并配置对应 key 或服务地址。

## 规则内容与归因

这个项目的代码采用 MIT License，详见 [LICENSE](LICENSE)。第三方规则内容、商标和归因要求不被 MIT License 覆盖，额外说明见 [NOTICE](NOTICE)。

插件随包包含一小组 DND 2024 / SRD 5.2 风格的规则索引卡，用于让 `query_core_rules` 在离线状态下返回简短摘要、流程提示、关键词和来源路径。它们是规则参考卡，不是规则书正文，不包含完整规则文本，也不是官方译本。中文字段用于检索和跑团裁定辅助；需要严肃发布、商业使用或二次分发时，请自行核对 SRD 版本、CC BY 4.0 attribution、商标使用和翻译边界。

This work includes material from the System Reference Document 5.2 ("SRD 5.2") by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

Dungeons & Dragons, D&D, Wizards of the Coast, and related names are trademarks of Wizards of the Coast LLC. This project is not affiliated with or endorsed by Wizards of the Coast LLC.

## 架构概览

```text
astrbot_plugin_auto_trpg_dm/
  main.py                  # AstrBot 插件入口与 /dm 事件处理
  core/
    router.py              # Intent Router，多步工具调用与模式切换
    models.py              # GameSession、角色、战斗和周期状态模型
    prompts.py             # 系统提示与模式提示
    external_memory.py     # Honcho 外置记忆适配
    ambient_image.py       # 氛围图片 provider、安全校验和物化逻辑
  tools/
    registry.py            # 按模式挂载工具
    memory_tools.py        # 角色、场景、世界设定和存档工具
    spatial_tools.py       # 战棋空间工具
    turn_tools.py          # 轮次、超时和行动推进
    rule_tools.py          # 本地规则执行
    rulebook_tools.py      # DND 2024 / DM guidance 检索
    map_tools.py           # 视觉地图生成
    ambient_image_tools.py # 氛围图片触发、prompt 和元数据保存
  rules/
    python_runtime.py      # 受限 Python 规则运行时
    dice.py                # 骰子工具
  rulebook/
    store.py               # 本地规则卡存储
    retriever.py           # 规则检索
  spatial/
  storage/
```

核心思路是把“事实”和“叙事”分开：

- 坐标、视线、距离、掩体、数值结算由本地工具负责。
- LLM 负责理解玩家自然语言、组织裁定、调用工具和输出叙事。
- 存档、规则执行和审计结果写回本地 JSON，避免只存在上下文里。

## 本地测试

```powershell
python -m compileall -q astrbot_plugin_auto_trpg_dm tests scripts
python -m pytest -q
git diff --check
```

GitHub Actions 会在 push 和 PR 上运行：

- `python -m pip install -r astrbot_plugin_auto_trpg_dm/requirements.txt`
- `python -m compileall -q astrbot_plugin_auto_trpg_dm tests`
- `python -m pytest -q`

## 发布建议

这个仓库目前是开发仓库，包含测试、文档和部署脚本。准备提交 AstrBot 插件市场时，建议拆出一个独立稳定版仓库，仓库名使用 `astrbot_plugin_auto_trpg_dm`，远程仓库建议使用 `https://github.com/unhumanplane/astrbot_plugin_auto_trpg_dm`，保留：

- `astrbot_plugin_auto_trpg_dm/`
- `README.md`
- `CHANGELOG.md`
- `.github/workflows/pr-check.yml`
- `LICENSE`，代码使用 MIT License
- `NOTICE`，说明 SRD 5.2 归因、规则卡边界和商标关系

不要带入 `.deploy/`、`.recovery/`、`tests/_tmp_runtime/`、本地地图输出、原始规则书资料、NAS 配置、SSH 私钥或真实 API key。

## 相关文档

- [CHANGELOG.md](CHANGELOG.md)
- [docs/honcho-external-memory.md](docs/honcho-external-memory.md)
- [docs/ambient-image-api.md](docs/ambient-image-api.md)
- [docs/dm-outbound-cleanup.md](docs/dm-outbound-cleanup.md)
- [docs/design.zh.md](docs/design.zh.md)
- [docs/architecture_spec.md](docs/architecture_spec.md)

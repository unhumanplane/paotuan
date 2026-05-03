# AstrBot Auto TRPG DM

全自然语言 TRPG DM 插件，基于 AstrBot v4.5.7+。当前插件版本：`v0.1.85`。

这个项目的目标不是做一组零散命令，而是在 AstrBot 里运行一个可长期维护的小型 TRPG runtime。玩家可以直接说“我靠墙潜行过去，再射最近的敌人”，插件会结合当前场景、角色状态、战棋事实、本地规则和 LLM 裁定完成回应。

[![PR checks](https://github.com/unhumanplane/paotuan/actions/workflows/pr-check.yml/badge.svg)](https://github.com/unhumanplane/paotuan/actions/workflows/pr-check.yml)

## 适合什么场景

- 纯文字跑团，希望玩家像聊天一样行动、追问和推进剧情。
- 带轻量战棋或位置概念的团，需要明确移动、视线、距离、掩体和轮次。
- 想让 AI 做 DM / 协同 DM，但仍希望关键事实、数值和规则执行有本地约束。
- 需要长期存档、审计、恢复、部署和 PR 流程，而不是一次性 demo。

它不是“全自动完美 DM”。更准确地说，它是一套把自然语言入口、状态机、工具调用、战棋事实、本地规则和可选外置记忆组合起来的工程骨架。

## 当前能力

### 自然语言入口

- 默认使用 `/dm` 作为显式入口，避免普通群聊被误接入 LLM。
- 支持 `/DM`、`/Dm`、`/dM` 等大小写误用。
- 玩家不需要记忆 `/move`、`/attack`、`/roll` 这类命令，Intent Router 会按场景选择工具。
- 普通 DM 回复会抑制“1/2/3 选项”“还是 A/B/C”“下一步菜单”这类行动菜单，让玩家直接描述想尝试的行动；设计边界见 [docs/dm-outbound-cleanup.md](docs/dm-outbound-cleanup.md)。

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

### 规则与裁定

- 内置受限 Python 规则运行时，可注册和执行本地规则函数。
- `execute_rule` 负责骰子、命中、豁免、伤害、治疗等数值结算。
- `query_core_rules` 用于查询本地 DND 2024 规则摘要和 DM guidance。
- 规则书内容按需检索，不会把整本规则长期塞进 prompt 或存档。

### 可选 Honcho 外置记忆

Honcho 是可选增强层，默认关闭。它不替代本地 JSON 存档，只用于辅助回忆玩家偏好、角色倾向、伏笔、幕间 recap 和关键事件。

支持模式：

- `auto`：有自托管地址时用 self-hosted，否则用 cloud。
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

### 可选氛围图片

氛围图片是可选视觉辅助，默认关闭，不接受玩家直接命令生图。它和 SVG 战棋地图是两套功能：SVG 地图用于位置、距离、视线和战场示意；氛围图只用于渲染剧情气氛、帮助玩家理解关键场景，不会写入任何权威游戏事实。

当前接入目标是 PackyAPI `gpt-image-2`，默认走 `/v1/images/generations`，也可以切换到 `/v1/chat/completions`。图片 API key、base URL、模型、尺寸、质量、返回格式、触发频率、活跃度门禁、prompt 语义去重和 prompt 模板都通过 AstrBot 插件配置设置。prompt 模型会先返回 `title`、`prompt`、`style`，插件再拼合成最终生图 prompt；生成完成后单独发送 `{title}` 和图片，不附着到普通 `/dm` 回复上。

安全边界：

- 下载 provider 返回的 URL 前会阻止 localhost、私网、link-local、reserved、multicast 等地址。
- provider 响应、URL 下载和 `b64_json` 图片都有大小上限。
- 图片下载会校验 content type。
- 普通 `/dm` 回复不会等待图片生成，图片生成在后台任务中独立完成。

完整配置、触发规则、隐私边界和费用风险见 [docs/ambient-image-api.md](docs/ambient-image-api.md)。

## 架构概览

```text
astrbot_plugin_auto_trpg_dm/
  main.py                 # AstrBot 插件入口与 /dm 事件处理
  core/
    router.py             # Intent Router，多步工具调用与模式切换
    models.py             # GameSession、角色、战斗和周期状态模型
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

核心思路是把“事实”和“叙事”分开：

- 坐标、视线、距离、掩体、数值结算由本地工具负责。
- LLM 负责理解玩家自然语言、组织裁定、调用工具和输出叙事。
- 存档、规则执行和审计结果写回本地 JSON，避免只存在上下文里。

## 快速开始

### 1. 放入 AstrBot 插件目录

把仓库中的 `astrbot_plugin_auto_trpg_dm/` 放到 AstrBot 插件目录，插件入口为：

```text
astrbot_plugin_auto_trpg_dm/main.py
```

AstrBot 识别插件后，会读取：

```text
astrbot_plugin_auto_trpg_dm/metadata.yaml
astrbot_plugin_auto_trpg_dm/_conf_schema.json
```

### 2. 最小配置

默认情况下，只需要在聊天里使用 `/dm`：

```text
/dm 我想开一个黑暗奇幻调查团，角色是一个谨慎的游荡者。
```

建议保持：

- `allow_private_chat=false`
- `honcho_enabled=false`
- `ra_enabled=false`

等基础流程稳定后，再逐项打开外置记忆或 RA。

### 3. 常用配置项

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled_sessions` | 空列表 | 允许自动接管的会话 ID。通常留空，只用 `/dm`。 |
| `trigger_prefixes` | `/dm` | 自定义触发词。 |
| `allow_private_chat` | `false` | 是否允许私聊不带 `/dm` 也触发。建议关闭。 |
| `honcho_enabled` | `false` | 是否启用 Honcho 外置记忆。 |
| `honcho_target` | `auto` | `auto` / `cloud` / `self_hosted`。 |
| `honcho_timeout_seconds` | `8` | Honcho 单轮读写超时，失败会降级。 |
| `honcho_cross_campaign_personalization_enabled` | `false` | 是否允许跨团复用玩家偏好。 |
| `ra_enabled` | `false` | 是否启用 Recorder Agent。 |
| `ra_model_provider` | `default` | RA 使用的模型 provider。 |
| `ra_max_tokens` | `2048` | RA 输出 token 上限建议值。 |
| `ambient_image_enabled` | `false` | 是否启用 TRPG 氛围图片。 |
| `ambient_image_api_mode` | `images` | 图片 API 路径：`images` 或 `chat_completions`。 |
| `ambient_image_base_url` | `https://www.packyapi.com` | 图片 API base URL。 |
| `ambient_image_api_key_env` | `PACKYAPI_SORA_API_KEY` | 图片 API key 所在环境变量名。 |
| `ambient_image_frequency` | `medium` | 普通氛围图触发频率。 |
| `ambient_image_activity_window_minutes` | `60` | 普通氛围图活跃窗口。 |
| `ambient_image_activity_min_messages` | `10` | 活跃窗口内最少玩家消息数。 |
| `ambient_image_activity_min_players` | `2` | 活跃窗口内最少不同玩家数。 |

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
```

这些数据不应该提交到 Git。仓库 `.gitignore` 已经忽略：

- `.deploy/`
- `.recovery/`
- `astrbot_plugin_auto_trpg_dm/data/`
- `data/`
- `dnd2024_core_rules/`
- 本地地图输出 `latest_*.png`、`latest_*.svg`、`map_*_remote.png`
- SSH key、`.env`、数据库、日志和 Python 缓存

NAS 地址、部署路径、SSH 私钥和真实 API key 都应只留在本机 `.deploy/` 或环境变量中。

## NAS 部署

仓库提供了本地部署脚本，目标是方便但可审计：只部署当前 Git `HEAD`，工作区不干净时默认拒绝部署，部署前运行编译检查，NAS 端先备份旧目录再替换。

初始化本地配置：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy-nas.ps1 -Init
```

编辑生成的本地配置：

```text
.deploy/nas-deploy.json
```

部署当前提交：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy-nas.ps1
```

拉取最新 `main` 后部署：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy-nas.ps1 -Pull
```

这个脚本只通过 `git archive` 打包 `astrbot_plugin_auto_trpg_dm/`，不会带上 `.deploy/`、私钥、缓存、运行数据或本地规则书原始资料。

重启命令默认有 120 秒硬超时，可在本地配置里调整：

```json
{
  "restartCommand": "docker restart astrbot",
  "restartTimeoutSeconds": 120
}
```

如果 NAS 上 Docker / Container Manager 对某个容器的 `inspect`、`restart` 或 `kill` 调用卡住，脚本会在超时后失败并报告；此时插件文件可能已经替换成功，但运行中的 AstrBot 进程未必已加载新版本。可以先用 `-SkipRestart` 只更新文件，再在 DSM 或具备 sudo 权限的 SSH 会话中重启 Container Manager / AstrBot 容器。

## PR 工作流

GitHub Actions 会在 push 和 PR 上运行：

- `python -m compileall -q astrbot_plugin_auto_trpg_dm tests`
- `python -m pytest -q`

本地可以用脚本辅助检查 PR：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/handle-pr.ps1 -PrNumber 123
```

检查通过后合并：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/handle-pr.ps1 -PrNumber 123 -Merge
```

合并后部署到 NAS：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/handle-pr.ps1 -PrNumber 123 -Merge -DeployAfterMerge
```

合并前建议额外确认：

- `git diff --check`
- 没有 NAS 配置、私钥、真实 API key 或运行数据进入 diff
- PR 和最新 `main` 没有冲突
- 新功能默认关闭或具备清晰降级路径

## 本地测试

建议至少运行：

```powershell
python -m compileall -q astrbot_plugin_auto_trpg_dm tests scripts
git diff --check
```

如果本地装了 `pytest`：

```powershell
python -m pytest -q
```

当前开发机如果没有安装 `pytest`，部署脚本会跳过本地 pytest，但 GitHub Actions 仍会在远端完整运行。

## 相关文档

- [CHANGELOG.md](CHANGELOG.md)
- [docs/honcho-external-memory.md](docs/honcho-external-memory.md)
- [docs/ambient-image-api.md](docs/ambient-image-api.md)
- [docs/design.zh.md](docs/design.zh.md)
- [docs/architecture_spec.md](docs/architecture_spec.md)
- [DND2024_CORE_RULEBOOK_INTEGRATION_PLAN.md](DND2024_CORE_RULEBOOK_INTEGRATION_PLAN.md)

## 当前开发重点

- 让自然语言跑团的状态推进更稳定。
- 降低 prompt 上下文成本，减少规则列表和历史摘要重复。
- 强化本地工具对事实、数值和回合约束的控制。
- 保持 Honcho、RA、NAS 部署等增强能力默认关闭、可降级、可审计。
- 每次发布都补充 `CHANGELOG.md`，让 NAS 和 GitHub 上的版本变更可追踪。

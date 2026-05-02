# Changelog

所有已经合入 `main` 的用户可见变更都记录在这里。日期使用香港时区对应的开发日期。

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

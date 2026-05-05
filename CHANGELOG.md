# Changelog

所有已经合入 `main` 的用户可见变更都记录在这里。日期使用香港时区对应的开发日期。

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

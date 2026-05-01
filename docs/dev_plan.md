# Dual Agent 架构开发计划

> **任务背景**：将当前单体 TRPG DM 拆分为 DM Agent（创意导演）+ RA Recorder Agent（状态记录员）的双 Agent 架构。DM 负责叙事与工具调用，RA 负责周期结束时的结构化状态规范化。详见 `design.md` / `design.zh.md` 与 `architecture_spec.md`。
>
> **目标分支**：`sandbox/hashval/double-agent`
> **预估工期**：5–7 天
> **状态**：设计已完成，待进入开发

---

## 里程碑总览

| 里程碑 | 内容 | 预估工期 |
|--------|------|----------|
| M1 | 数据模型 + 周期状态机 | 1 天 |
| M2 | Prompt 基础设施 | 0.5–1 天 |
| M3 | DM 侧周期集成 | 1 天 |
| M4 | Recorder Agent + 完整流水线 | 1–2 天 |
| M5 | 配置 + Audit + 功能开关 | 0.5–1 天 |

---

## PR 1：Foundation — 数据模型 + 周期状态机

**目标**：铺好数据结构，零行为变更。当前代码能无痛合入，现有存档兼容。

### 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `core/cycle_state_machine.py` | 新建 | `CycleStateMachine` 类 |
| `core/models.py` | 修改 | 新增 `CycleState`、`AuditBuffer`、`RACycleInput`、`CycleAction`；`GameSession` 扩展字段 |
| `_conf_schema.json` | 修改 | 新增 `ra_enabled` 总开关，默认 `false` |
| `tests/test_models.py` 或新建 `tests/test_cycle_state_machine.py` | 新建/修改 | 序列化 + 状态转换测试 |

### 核心变更

- 新增 `CycleState` 枚举：`CYCLE_ACTIVE`、`CYCLE_RESOLVING`、`CYCLE_TRANSITION`
- 新增 `AuditBuffer` dataclass（完整审计数据，含 `player_message`）：`cycle_id`、`actions`、`started_at`、`ended_at`
- 新增 `RACycleInput` dataclass（RA 专用过滤投影，不含 `player_message` / PII / diagnostics / prompts / raw audit）：`cycle_id`、`actions`（仅 `dm_narrative` + 清洗后的 `tools_called`）
- 新增 `CycleAction` dataclass：`player_id`、`character_id`、`player_message`、`dm_narrative`、`tools_called`、`timestamp`
- `GameSession` 新增字段（全部带默认值，向后兼容）：
  - `cycle_state: CycleState = CycleState.CYCLE_ACTIVE`
  - `audit_buffer: AuditBuffer = field(default_factory=AuditBuffer)`
  - `ra_cycle_input: RACycleInput = field(default_factory=RACycleInput)`
  - `current_cycle_id: int = 0`
  - `environment_summaries: list[dict] = field(default_factory=list)`
  - `rule_sets: dict[str, Any] = field(default_factory=dict)`
- `_conf_schema.json` 新增 `ra_enabled: bool`，默认 `false`，为后续 PR 提供行为短路开关

### 验收标准

- [ ] 现有旧存档 JSON 能正常加载，新字段自动填充默认值
- [ ] 新存档序列化/反序列化无数据丢失
- [ ] `CycleStateMachine` 三个状态转换全部通过单元测试
- [ ] `ra_enabled` 默认关闭，新增字段后插件行为不发生变化
- [ ] `pytest -q` 全绿

### 风险

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| 模型字段变更导致存档不兼容 | 低 | 所有新字段使用 `default_factory`；`from_dict` 使用 `.get()` 安全取值 |

---

## PR 2：Prompt 基础设施 — BASE_RULES + RA Prompt

**目标**：把 Prompt 层准备好，DM System Prompt 先接入 BASE_RULES；RA 和 Cycle Start Prompt 的 builder 就绪。

### 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `core/prompts.py` | 修改 | 新增 `BASE_RULES`、`build_ra_system_prompt()`、`build_cycle_start_prompt()`；修改 `build_system_prompt()` |

### 核心变更

- 提取 `BASE_RULES` 常量（元机制、禁止行为、通用约束），注入 DM 和 RA 的 System Prompt
- `build_ra_system_prompt()`：RA 的 System Prompt，强调" strictly follow DM narration, never override, output JSON only"
- `build_cycle_start_prompt()`：框架生成，供 DM 进入下一周期时读取
- `build_system_prompt()`：新增 `BASE_RULES` 段落；预留 `{ra_summary}` 插槽（此 PR 不接入真实数据，仅占位）

### 验收标准

- [ ] DM 输出的 System Prompt 包含 `BASE_RULES` 全文
- [ ] `build_ra_system_prompt()` 输出符合设计文档中 RA 角色定义
- [ ] `build_cycle_start_prompt()` 输出包含周期摘要、角色状态、世界变更
- [ ] Prompt 长度估算不超限（当前 System Prompt 约 150 条规则，新增 BASE_RULES 需控制增量）

### 风险

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| BASE_RULES 过长导致 token 爆炸 | 中 | 控制 BASE_RULES 在 800 字以内；必要时拆分为 "core" + "extended" |
| Prompt 变更影响现有 DM 行为 | 低 | 此 PR 仅注入常量，不删除现有规则 |

---

## PR 3：DM 侧周期集成 — Audit Buffer + Cycle Control

**目标**：让 DM 能积累行动到 Audit Buffer（完整数据）和 RA 输入投影（过滤数据），能显式结束周期。RA 暂不运行（feature flag 关闭），确保主干可用。

### 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `core/router.py` | 修改 | 每次行动后追加 `audit_buffer` 和 `ra_cycle_input`；处理 `cycle_control(action="end_cycle")` |
| `tools/registry.py` | 修改 | 新增 `cycle_control` 工具 |
| `main.py` | 修改 | `_handle_dm_event()` 增加 `cycle_state` 门控 |
| `tests/` | 新建/修改 | Cycle buffer 累积测试、周期结束检测测试 |

### 核心变更

- **Audit Buffer 累积**：`IntentRouter._handle_message_once()` 中，DM 工具循环结束后，若判定为"行动"（非查询），将完整数据追加到 `session.audit_buffer.actions`，同时生成过滤后的 `session.ra_cycle_input`（不含 `player_message` / PII / diagnostics / prompts / raw audit；工具 args/result 必须先清洗）
- **Cycle Control 工具**：`cycle_control(action="end_cycle")`，仅 DM 可调用；MVP 不提供文本标记或启发式周期结束路径
- **周期结束检测**：DM 显式调用 `cycle_control(action="end_cycle")` → `cycle_state` 变为 `CYCLE_RESOLVING`
- **门控**：`main.py` 在 `cycle_state != CYCLE_ACTIVE` 时，允许 read-only local fast paths（如 status/token/help 类查询），阻止或排队 mutating local fast paths 和会进入 DM LLM 的新行动
- **Feature Flag**：读取 PR 1 已加入的 `ra_enabled` 配置项；默认 `false` 时，RA 逻辑短路，不影响现有行为

### 验收标准

- [ ] 玩家声明行动后，`session.audit_buffer.actions` 和 `session.ra_cycle_input.actions` 正确追加记录
- [ ] 玩家查询状态（如 `/dm status`）不写入 cycle buffer
- [ ] DM 调用 `cycle_control(action="end_cycle")` 后，`cycle_state` 变为 `CYCLE_RESOLVING`
- [ ] 周期结算期间，read-only local fast paths 可返回结果；新行动或 mutating fast paths 收到等待提示，不进入 LLM
- [ ] `ra_enabled=false` 时，周期结束后直接切回 `CYCLE_ACTIVE`，不调用 RA
- [ ] `CYCLE_ACTIVE` 内回合系统（turn_control）正常推进；`CYCLE_RESOLVING` / `CYCLE_TRANSITION` 中不执行新的 mutating turn_control 路径

### 风险

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| router.py 改动触及核心 pipeline，引入回归 | 中 | 完整跑一遍现有测试；新增 cycle buffer 逻辑用独立函数封装，最小化侵入 |
| 周期结束误判（查询被当行动） | 中 | 复用现有 `_looks_like_stateful_player_message()` 判断逻辑 |

---

## PR 4：Recorder Agent + 完整流水线

**目标**：RA 真正运行，双 Agent 流水线闭环。这是核心功能 PR。

### 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `core/environment_agent.py` | 新建 | `RecorderAgent` 类 |
| `core/router.py` | 修改 | 周期结束后触发 RA；保存 RA 输出；推进状态机 |
| `core/prompts.py` | 修改 | `build_system_prompt()` 正式接入上一周期 RA summary |
| `tests/test_environment_agent.py` | 新建 | RA 输出 schema 验证、失败处理测试 |

### 核心变更

- **RecorderAgent**：
  - `run_cycle_resolution(session: GameSession) -> dict`
  - 读取：`ra_cycle_input`（过滤投影）、`BASE_RULES`、清洗后的权威字段快照；不得读取完整 `GameSession`
  - 调用：通过项目已有 LLM 调用封装执行一次逻辑 RA 生成；如果当前只存在 `astr_context.llm_generate()`，先包出小适配器并复用既有 retry/fallback 语义
  - 输出：解析 JSON，返回 cycle summary 与 allowlisted patch candidates
  - RA **无工具访问权限**，纯文本输入 → JSON 输出
- **Router 集成**：
  - `cycle_state == CYCLE_RESOLVING` 时，调用 `RecorderAgent.run_cycle_resolution()`
  - 保存 RA 摘要到 `session.environment_summaries`；对 `session.characters` / `session.scene` 只应用 allowlisted、tool-backed、validator 通过的权威字段补丁
  - 生成 `cycle_start_prompt`，推进到 `CYCLE_TRANSITION`
  - RA 成功且补丁验证通过后，清空 `audit_buffer` 和 `ra_cycle_input`，`current_cycle_id += 1`
  - 推进到 `CYCLE_ACTIVE`
- **DM Prompt 接入**：`build_system_prompt()` 的 `{ra_summary}` 插槽正式填入 `session.environment_summaries[-1]`

### 验收标准

- [ ] 周期结束后，RA 只运行 **一次** LLM 调用
- [ ] RA 输出是有效 JSON，包含：`cycle_id`、`summary`、`character_status`、`enemy_status`、`world_changes`、`rules_triggered`、`dm_narrative_aligned`、`discrepancies`；其中状态字段均视为补丁候选
- [ ] RA 摘要保存到 `session.environment_summaries`，权威字段只通过 allowlisted、tool-backed、validator 通过的补丁改变
- [ ] 下一周期 DM 的 System Prompt 包含 RA summary + `discrepancies`
- [ ] DM Agent 在 `discrepancies` 非空时，用合理的场内解释圆回叙事冲突
- [ ] RA 失败（超时、无效 JSON、异常）时不阻塞游戏：保留未消费 `audit_buffer`、记录 recoverable error；如生成最小状态补丁，也必须来自 tool trace 且通过同一 allowlist/validator；随后直接回到 `CYCLE_ACTIVE`
- [ ] 战斗回合（turn_control）与周期边界互不干扰

### 风险

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| RA JSON 输出不稳定 | 高 | 要求 LLM 输出 JSON mode；增加 retry + fallback；无效 JSON 时保留 buffer、生成最小补丁、记录 recovery log |
| RA 运行时间导致玩家等待 | 中 | RA 是同步阻塞的，但只在周期结束运行一次；未来可优化为后台异步 |
| Token 成本超预期 | 中 | MVP 使用同一模型；后续 PR 可切换便宜模型 |

---

## PR 5：配置 + Audit + 功能开关

**目标**：可配置、可观测、可灰度上线。

### 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `_conf_schema.json` | 修改 | 新增 RA 模型和 token 相关配置项 |
| `storage/json_repository.py` | 修改 | audit log 记录 RA 执行 |
| `core/router.py` / `main.py` | 修改 | 读取配置，控制 RA 启用/禁用 |
| `docs/` | 更新 | 更新设计文档，标注已实现项 |

### 核心变更

- 配置项：
  - `ra_model_provider: str` — RA 使用的模型（默认 `"default"`，即同 DM）
  - `ra_max_tokens: int` — RA 输出 token 上限（默认 `2048`）
- Audit：RA 执行前后均写入 audit log（输入摘要、输出摘要、耗时、是否失败）
- 灰度策略：沿用 PR 1 的 `ra_enabled`；`ra_enabled=false` 时，整个双 Agent 逻辑短路，行为完全回到旧版

### 验收标准

- [ ] `ra_enabled=false` 时，游戏行为与旧版完全一致
- [ ] `ra_enabled=true` 时，双 Agent 流水线完整运行
- [ ] audit log 包含 RA 执行记录
- [ ] 配置热加载无需重启插件

### 风险

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| 配置读取失败导致功能异常 | 低 | 所有配置项有默认值；读取异常时 fallback 到 `ra_enabled=false` |

---

## 依赖关系

```
PR 1 (模型+状态机)
    │
    ├──→ PR 3 (DM 周期集成)
    │        │
    │        ├──→ PR 4 (RA + 闭环)
    │        │        │
    │        │        └──→ PR 5 (配置+开关)
    │        │
    └──→ PR 2 (Prompt 基建)
             │
             └──→ PR 4 (RA + 闭环)
```

**建议合入顺序**：`PR 1 → PR 2 → PR 3 → PR 4 → PR 5`

- PR 1 和 PR 2 互不依赖，可并行开发，但建议按顺序合入减少认知负担。
- PR 3 依赖 PR 1（需要 CycleState 和 AuditBuffer）。
- PR 4 依赖 PR 1 + PR 2 + PR 3。
- PR 5 依赖 PR 4。

---

## 分支策略

```
main (保持稳定，可发布)
  │
  └── sandbox/hashval/double-agent (开发基线)
        │
        ├── pr/1-foundation-cycle-state-machine
        ├── pr/2-prompt-infrastructure
        ├── pr/3-dm-cycle-integration
        ├── pr/4-recorder-agent
        └── pr/5-config-audit-feature-flag
```

- 每个 PR 从 `sandbox/hashval/double-agent` 切出独立分支。
- PR 合入后，`sandbox/hashval/double-agent` 快进合并。
- 全部完成后，`sandbox/hashval/double-agent` 提 PR 合入 `main`。

---

## 编码约定（此任务专用）

1. **所有 LLM 调用统一走项目已有 LLM 调用封装**；如果 RA 需要新增封装，必须复用现有 `self.astr_context.llm_generate()` 的 retry/fallback 语义，不能另开一套不一致的调用路径。
2. **RA 返回必须是 JSON**。如果模型不支持 JSON mode，在 prompt 中强制要求 `"output must be valid JSON only"`，并在代码层做 `try/except json.loads`。
3. **新字段必须有默认值**。`GameSession` 是核心数据模型，任何字段变更必须向后兼容旧存档。
4. **工具返回格式不变**。保持 `{"ok": bool, ...}`，RA 不调用工具，但读取工具执行结果。
5. **Audit Buffer 不清除历史**。只有 RA 成功且补丁验证完成后，才清空当前 buffer；`environment_summaries` 保留周期摘要（用于游戏结束统计和 debug）。失败的 RA 运行不得清空 `audit_buffer`。

---

## 待确认事项

开发开始前，请确认以下事项：

1. [x] **D1: 周期结束信号方式** — 使用 `cycle_control(action="end_cycle")` 工具（显式调用），不使用文本匹配或框架启发式
2. [x] **D2: RA 模型选择** — MVP 使用同一 provider；预留 `ra_model_provider` / `ra_max_tokens`，但不新建独立 provider 栈
3. [x] **D3: MemoryCompressor 与 RA 的关系** — MVP 中并存；RA 摘要可作为后续更高保真度压缩输入，不替代 `memory_summary`
4. [ ] **D4: Audit Buffer 上限** — 单周期最多保留多少条 action？建议 50 条；超限策略不得把 raw audit 直接写入 `environment_summaries`，需另走审计存储或安全摘要

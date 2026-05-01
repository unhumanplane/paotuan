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
| `tests/test_models.py` 或新建 `tests/test_cycle_state_machine.py` | 新建/修改 | 序列化 + 状态转换测试 |

### 核心变更

- 新增 `CycleState` 枚举：`CYCLE_ACTIVE`、`CYCLE_RESOLVING`、`CYCLE_TRANSITION`
- 新增 `AuditBuffer` dataclass（完整审计数据，含 `player_message`）：`cycle_id`、`actions`、`started_at`、`ended_at`
- 新增 `RACycleInput` dataclass（RA 专用过滤投影，不含 `player_message`）：`cycle_id`、`actions`（仅 `dm_narrative` + redacted `tools_called`）
  - `tools_called` redaction：仅保留工具名和状态变更字段（hp/position/alive 等），args 中 PII/诊断参数脱敏
  - 必要 ID 使用会话内 pseudonym（`pc_001`、`npc_orc_b`）代替真实 player_id
- 新增 `CycleAction` dataclass：`player_id`（audit 用真实 ID，`ra_cycle_input` 中替换为 pseudonym）、`character_id`、`player_message`、`dm_narrative`、`tools_called`（完整，redaction 由框架在生成 projection 时处理）、`timestamp`
- `GameSession` 新增字段（全部带默认值，向后兼容）：
  - `cycle_state: CycleState = CycleState.CYCLE_ACTIVE`
  - `audit_buffer: AuditBuffer = field(default_factory=AuditBuffer)`
  - `ra_cycle_input: RACycleInput = field(default_factory=RACycleInput)`
  - `current_cycle_id: int = 0`
  - `environment_summaries: list[dict] = field(default_factory=list)`
  - `rule_sets: dict[str, Any] = field(default_factory=dict)`

### 验收标准

- [ ] 现有旧存档 JSON 能正常加载，新字段自动填充默认值
- [ ] 新存档序列化/反序列化无数据丢失
- [ ] `CycleStateMachine` 三个状态转换全部通过单元测试
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
| `tests/test_prompts.py` | 新建 | `build_system_prompt()` 包含 BASE_RULES；`build_ra_system_prompt()` 符合 RA 角色定义；prompt 长度不超限 |

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
- [ ] `test_prompts.py` 全绿：`_inject_base_rules()` 在 prompt 头部注入；`_inject_ra_summary()` 占位符格式正确；`build_ra_system_prompt()` 包含 "output JSON only" 指令

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
| `core/router.py` | 修改 | **仅插入 2 行 hook 调用**：`_maybe_append_cycle_buffers()` 和 `_maybe_resolve_cycle()`。复杂逻辑下沉到 `CycleStateMachine`。 |
| `tools/registry.py` | 修改 | 新增 `cycle_control` 工具 |
| `main.py` | 修改 | **仅插入 1 个 guard clause**：`_cycle_state_gate()`。周期门控逻辑封装在私有方法中。 |
| `tests/test_cycle_hooks.py` | 新建 | `_maybe_append_cycle_buffers()` 行动/查询区分；`_cycle_state_gate()` 状态拦截；`cycle_control` 工具状态转换；`ra_enabled=false` 短路行为 |

### 核心变更

- **Audit Buffer 累积**：`IntentRouter` 新增 `_maybe_append_cycle_buffers(session, result, tool_trace)` 私有方法。主流程 `handle_message()` 在 DM 工具循环结束后插入 **一行调用**。该方法内部：
  - 判定是否为"行动"（复用 `_looks_like_stateful_player_message()`）
  - 若是，追加完整数据到 `session.audit_buffer`
  - 调用 `CycleStateMachine.build_ra_projection(session)` 生成过滤后的 `session.ra_cycle_input`
- **Cycle Control 工具**：`cycle_control(action="end_cycle" / "start_cycle")`，仅 DM 可调用
- **周期结束检测**：DM 显式调用 `cycle_control(action="end_cycle")` → `cycle_state` 变为 `CYCLE_RESOLVING`
- **门控**：`main.py` 新增 `_cycle_state_gate(session) -> bool` 私有方法。`_handle_dm_event()` 插入 **一个 guard clause**（3 行）：若 `cycle_state != CYCLE_ACTIVE`，返回等待提示，不进入 LLM
- **Feature Flag**：`ra_enabled` 配置项（默认 `false`），此 PR 合入后 RA 逻辑短路，不影响现有行为

### 验收标准

- [ ] 玩家声明行动后，`session.audit_buffer.actions` 和 `session.ra_cycle_input.actions` 正确追加记录
- [ ] 玩家查询状态（如 `/dm status`）不写入 cycle buffer
- [ ] DM 调用 `cycle_control(action="end_cycle")` 后，`cycle_state` 变为 `CYCLE_RESOLVING`
- [ ] 周期结算期间，新 `/dm` 消息收到等待提示，不进入 LLM
- [ ] `ra_enabled=false` 时，周期结束后直接切回 `CYCLE_ACTIVE`，不调用 RA
- [ ] 回合系统（turn_control）不受周期状态影响，正常推进
- [ ] `router.py` 主流程改动不超过 5 行（2 个 hook 调用 + 条件判断）
- [ ] `main.py` 主流程改动不超过 3 行（1 个 guard clause）
- [ ] `test_cycle_hooks.py` 全绿：查询消息不写入 audit_buffer；行动消息正确生成 ra_cycle_input（不含 player_message）；`cycle_state != CYCLE_ACTIVE` 时 gate 拦截；`cycle_control("end_cycle")` 触发状态转换；`ra_enabled=false` 时所有 hook 短路

### 风险

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| router.py 改动触及核心 pipeline，引入回归 | 中 | **hook 模式**：主流程仅插入 2 行调用；复杂逻辑封装在独立私有方法和 `CycleStateMachine` 中。完整跑一遍现有测试。 |
| 周期结束误判（查询被当行动） | 中 | 复用现有 `_looks_like_stateful_player_message()` 判断逻辑 |

---

## PR 4：Recorder Agent + 完整流水线

**目标**：RA 真正运行，双 Agent 流水线闭环。这是核心功能 PR。

### 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `core/environment_agent.py` | 新建 | `RecorderAgent` 类 |
| `core/router.py` | 修改 | **仅修改 `_maybe_resolve_cycle()` hook**：周期结束时触发 `RecorderAgent`，保存输出后推进状态机。主流程不变。 |
| `core/prompts.py` | 修改 | **仅插入 2 行调用**：`_inject_base_rules()` + `_inject_ra_summary()`。`build_system_prompt()` 主逻辑不变。 |
| `tests/test_environment_agent.py` | 新建 | RA 输出 schema 验证、失败处理测试 |

### 核心变更

- **RecorderAgent**：
  - `run_cycle_resolution(session: GameSession) -> dict`
  - 读取：`ra_cycle_input`（过滤投影）、`BASE_RULES`、`session` 快照（权威字段）
  - 调用：`astr_context.llm_generate()` 一次
  - 输出：解析 JSON，返回 cycle summary
  - RA **无工具访问权限**，纯文本输入 → JSON 输出
- **Router 集成**：
  - `cycle_state == CYCLE_RESOLVING` 时，调用 `RecorderAgent.run_cycle_resolution()`
  - 保存 RA 输出到 `session.environment_summaries`、更新 `session.characters` / `session.scene`
  - 生成 `cycle_start_prompt`，推进到 `CYCLE_TRANSITION`
  - 清空 `audit_buffer` 和 `ra_cycle_input`，`current_cycle_id += 1`
  - 推进到 `CYCLE_ACTIVE`
- **DM Prompt 接入**：`build_system_prompt()` 内部插入 **两行调用**：
  - `_inject_base_rules(prompt)` — 在 prompt 头部注入 `BASE_RULES`
  - `_inject_ra_summary(prompt, session)` — 在 prompt 尾部注入上一周期 RA summary + discrepancies
  主 prompt 组装逻辑完全不变，仅通过 hook 拼接额外段落。

### 验收标准

- [ ] 周期结束后，RA 只运行 **一次** LLM 调用
- [ ] RA 输出是有效 JSON，包含：`cycle_id`、`summary`、`character_status`、`enemy_status`、`world_changes`、`rules_triggered`、`dm_narrative_aligned`、`discrepancies`
- [ ] RA 输出保存到 `session.environment_summaries`
- [ ] 下一周期 DM 的 System Prompt 包含 RA summary + `discrepancies`
- [ ] DM Agent 在 `discrepancies` 非空时，用合理的场内解释圆回叙事冲突
- [ ] RA 失败（超时、无效 JSON、异常）时不阻塞游戏：保留未消费 `audit_buffer`、记录 recoverable error、从 tool trace 生成最小状态补丁、直接回到 `CYCLE_ACTIVE`
- [ ] 战斗回合（turn_control）与周期边界互不干扰
- [ ] `test_environment_agent.py` 全绿：RA 输出 JSON schema 校验通过；无效 JSON 时保留 audit_buffer、记录 recovery log、生成最小状态补丁；RA 失败后 `cycle_state` 回到 `CYCLE_ACTIVE`（跳过 `CYCLE_TRANSITION`）

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
| `_conf_schema.json` | 修改 | 新增 RA 相关配置项 |
| `storage/json_repository.py` | 修改 | audit log 记录 RA 执行 |
| `core/router.py` / `main.py` | 修改 | 读取配置，控制 RA 启用/禁用 |
| `tests/test_config_fallback.py` | 新建 | 配置缺失时 fallback 到 `ra_enabled=false`；`ra_enabled=false` 时双 Agent 逻辑完全短路；热加载生效 |
| `docs/` | 更新 | 更新设计文档，标注已实现项 |

### 核心变更

- 配置项：
  - `ra_enabled: bool` — 总开关（默认 `false`，向后兼容）
  - `ra_model_provider: str` — RA 使用的模型（默认 `"default"`，即同 DM）
  - `ra_max_tokens: int` — RA 输出 token 上限（默认 `2048`）
- Audit：RA 执行前后均写入 audit log（输入摘要、输出摘要、耗时、是否失败）
- 灰度策略：`ra_enabled=false` 时，整个双 Agent 逻辑短路，行为完全回到旧版

### 验收标准

- [ ] `ra_enabled=false` 时，游戏行为与旧版完全一致
- [ ] `ra_enabled=true` 时，双 Agent 流水线完整运行
- [ ] audit log 包含 RA 执行记录
- [ ] 配置热加载无需重启插件
- [ ] `test_config_fallback.py` 全绿：配置缺失或读取失败时 fallback 到 `ra_enabled=false`；`ra_enabled=false` 时 `_maybe_append_cycle_buffers()`、`_maybe_resolve_cycle()`、`_inject_ra_summary()` 全部短路，不抛异常；配置热加载后 `ra_enabled` 切换即时生效

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

1. **所有 LLM 调用统一走 `self.astr_context.llm_generate()`**，RA 也不例外。
2. **RA 返回必须是 JSON**。如果模型不支持 JSON mode，在 prompt 中强制要求 `"output must be valid JSON only"`，并在代码层做 `try/except json.loads`。
3. **新字段必须有默认值**。`GameSession` 是核心数据模型，任何字段变更必须向后兼容旧存档。
4. **工具返回格式不变**。保持 `{"ok": bool, ...}`，RA 不调用工具，但读取工具执行结果。
5. **Audit Buffer 不清除历史**。周期结束后清空当前 buffer，但 `environment_summaries` 保留所有周期摘要（用于游戏结束统计和 debug）。失败的 RA 运行不得清空 `audit_buffer`。
6. **现有文件只做最小化 hook**。`main.py` / `router.py` / `prompts.py` 的主流程只允许插入 **1–2 行调用**（如 `_maybe_append_cycle_buffers()`、` _inject_ra_summary()`）。所有复杂逻辑必须封装到：
   - 新增文件（`cycle_state_machine.py`、`environment_agent.py`）
   - 或现有类的私有方法中，且主流程不展开实现细节。
   这条规则确保与 Honcho 等并行功能合并时冲突最小。

---

## 待确认事项

开发开始前，请确认以下事项：

1. [x] **D1: 周期结束信号方式** — **确认使用 `cycle_control` 工具（显式调用）**。DM Agent 通过调用 `cycle_control(action="end_cycle")` 显式结束周期。不采用文本匹配或框架启发式。
2. [x] **D2: RA 模型选择** — **MVP 使用同一 provider（Option A）**，`ra_model_provider` 默认 `"default"` 即与 DM 同模型。后续 PR 可切换至 cheaper model（Option B），`astr_context.llm_generate()` 已支持 provider-agnostic 调用。
3. [x] **D3: MemoryCompressor 与 RA 的关系** — **Option B: 并存，RA 摘要作为 MemoryCompressor 的更高保真输入**。RA 的 `environment_summaries` 提供结构化周期摘要，MemoryCompressor 在压缩时优先读取这些结构化数据，而非仅依赖自由文本叙事。`memory_summary` 继续存在，作为 DM 的叙事上下文。PR 4 或后续优化中接入。
4. [x] **D4: Audit Buffer 上限** — **单周期最多保留 50 条 action**。超限时最早记录移入 `environment_summaries` 的 `overflow_actions` 字段，确保 RA 输入不会超限。

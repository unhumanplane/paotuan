# Architecture Spec: Dual-Agent TRPG — Current vs Target

> This document maps the current monolithic DM codebase against the dual-agent design,
> identifying every gap that must be closed for implementation.

---

## 1. Current Architecture (As-Is)

### 1.1 Message Pipeline

```
Player /dm message
  |
main.py _handle_dm_event()
  |-- local fast path (pause, status, token, turn, backup)
  |-- security precheck
  |-- dedup + pacing
  |
IntentRouter.handle_message()
  |-- load session
  |-- detect GameMode (NARRATIVE | CHARACTER_CREATION | RULE_AUTHORING | TACTICAL | RESOLUTION)
  |-- maybe compress memory
  |-- build system prompt + user prompt
  |-- mount tools per mode
  |-- _run_llm_tool_loop()  [max 8 steps, ReAct]
  |-- tool execution via LockedToolExecutor
  |-- persist narrative trace
  |-- maybe compress memory again
  |-- return completion
  |
Player sees DM response immediately
```

### 1.2 Key Components

| Component | File | Responsibility |
|-----------|------|---------------|
| Plugin Entry | `main.py` | Message routing, local fast paths, heartbeat, turn timeout |
| Intent Router | `core/router.py` | DM orchestration, multi-hop LLM tool loop, mode detection |
| GameSession | `core/models.py` | Session state: characters, rules, battle, scene, world_tags, memory_summary |
| Prompts | `core/prompts.py` | DM system prompt (~150 rules), user prompt with intent hints |
| Tool Registry | `tools/registry.py` | Dynamic tool mounting per GameMode |
| Turn Tools | `tools/turn_tools.py` | Combat round management (120s timeout) |
| Memory Compressor | `core/memory.py` | Free-text compression into `memory_summary` |
| Spatial Engine | `spatial/engine.py` | Grid physics, pathfinding, LoS |
| Rule Runtime | `rules/python_runtime.py` | Sandboxed Python rule execution |
| Storage | `storage/json_repository.py` | JSON persistence, audit logs, backups |

### 1.3 Data Model (Current)

```python
@dataclass
class GameSession:
    session_id: str
    mode: GameMode                      # NARRATIVE, TACTICAL, etc.
    title: str
    active_character_id: str
    participants: dict
    player_character_map: dict
    world_tags: dict
    scene: dict                         # summary, current_conflict, _recent_narrative_events
    memory_summary: str                 # free-text compressed narrative
    characters: dict[str, Character]
    rules: dict[str, RuleRef]
    battle: dict                        # grid, turn, entities
    created_at / updated_at
```

**No cycle concept. No RA. No structured action buffer.**

---

## 2. Target Architecture (To-Be)

### 2.1 Message Pipeline

```
Player /dm message
  |
main.py _handle_dm_event()
  |-- local fast paths (unchanged)
  |-- [NEW] check cycle_state: if CYCLE_RESOLVING/CYCLE_TRANSITION, block or queue
  |
IntentRouter.handle_message()  [DM Agent only]
  |-- load session
  |-- detect GameMode (unchanged)
  |-- build DM system prompt (now includes BASE_RULES + last RA summary + cycle context)
  |-- mount DM tools per mode
  |-- _run_llm_tool_loop()  [DM Agent multi-hop]
  |-- tool execution + capture tool trace
  |-- [NEW] delegate: _maybe_append_cycle_buffers()  (new file, minimal hook)
  |-- [NEW] delegate: _maybe_resolve_cycle()  (new file, minimal hook; gated by ra_enabled)
  |-- return completion
  |
Player sees DM response immediately
  |
[NEW] if cycle_end signal (from cycle_control tool):
  |
  CycleStateMachine.transition(CYCLE_ACTIVE -> CYCLE_RESOLVING)
  |
  [NEW] RecorderAgent.run()  [ONE LLM call; new module; no inline code in router.py]
    |-- read: ra_cycle_input projection, previous master state (authority snapshot), BASE_RULES
    |-- generate: structured JSON (cycle summary, character_status, enemy_status, world_changes)
    |-- save to session.environment_summaries, characters, scene
  |
  CycleStateMachine.transition(CYCLE_RESOLVING -> CYCLE_TRANSITION)
  |
  [NEW] build_cycle_start_prompt()  (new function in prompts.py; injected via _inject_cycle_start_context)
  |
  CycleStateMachine.transition(CYCLE_TRANSITION -> CYCLE_ACTIVE)
  |
  DM Agent next message receives: RA summary + cycle_start_prompt + full state
```

### 2.2 New Components

| Component | File | Responsibility |
|-----------|------|---------------|
| **Recorder Agent** | `core/environment_agent.py` | NEW. Runs once per cycle. LLM-based structured state normalizer. No tool access. Called via one-line hook from router. |
| **Cycle State Machine** | `core/cycle_state_machine.py` | NEW. Manages CYCLE_ACTIVE/RESOLVING/TRANSITION. Owns buffer append + projection logic. Router/main call one-line delegates. |
| **Audit Buffer + RA Input** | `core/models.py` fields | NEW. audit_buffer (full, audit-only) + ra_cycle_input (filtered, RA-consumable). |
| **BASE_RULES** | `core/prompts.py` | NEW. Shared constant injected into both DM and RA system prompts. |
| **RA Prompt Builder** | `core/prompts.py` | NEW. `build_ra_system_prompt()` + `build_ra_cycle_prompt()`. |

---

## 3. Gap Analysis — Component by Component

### 3.1 Data Model (`core/models.py`)

| Gap | Current | Target | Change |
|-----|---------|--------|--------|
| **G1: Missing Cycle State** | `GameMode` only | Need `CycleState` enum | Add `CycleState` with `CYCLE_ACTIVE`, `CYCLE_RESOLVING`, `CYCLE_TRANSITION` |
| **G2: Missing Audit Buffer** | None | `audit_buffer: AuditBuffer` | Add `AuditBuffer` dataclass with full action traces (incl. `player_message`); for audit/debug only |
| **G2b: Missing RA Input Projection** | None | `ra_cycle_input: RACycleInput` | Filtered projection from audit_buffer; RA allowlist excludes `player_message`, PII, diagnostics; IDs use pseudonyms; tool trace fields are redacted |
| **G3: Missing RA Output Storage** | None | `environment_summaries: list[dict]` | Add field to store RA cycle summary JSONs |
| **G4: Missing Cycle ID** | None | `current_cycle_id: int` | Add counter for cycle numbering |
| **G5: Missing Action Trace** | `_recent_narrative_events` is free-text | Need structured `CycleAction` with `dm_narrative`, `tools_called`, `player_id`, `character_id` | Replace/extend narrative trace format; split into audit vs. RA-consumable projections |
| **G6: Missing BASE_RULES Storage** | `world_tags` has ad-hoc rules | `rule_sets: dict` for structured campaign rules | Add field for DM-defined, RA-structured rules |

**Impact:** Medium. All additive fields; backward-compatible if defaults are provided.

### 3.2 Pipeline (`core/router.py`)

| Gap | Current | Target | Change |
|-----|---------|--------|--------|
| **G7: Single Agent** | `IntentRouter` = DM only | DM + RA orchestration | DM loop stays unchanged. Add one-line `_maybe_resolve_cycle()` hook in `handle_message()` that delegates to `CycleStateMachine` + `RecorderAgent`. |
| **G8: No Cycle End Detection** | Per-message processing | DM signals cycle end via tool | Add `cycle_control` tool. Router calls `CycleStateMachine.transition()` via one-line hook. No inline state logic in router. |
| **G9: No Tool Trace Capture** | Tool results logged to audit only | Tool results must feed into audit_buffer and ra_cycle_input | `CycleStateMachine.append_action()` handles full capture + projection generation. Router calls it via one-line hook `_maybe_append_cycle_buffers()`. |
| **G10: No RA Invocation** | None | Trigger RA after cycle end | `RecorderAgent.run_cycle_resolution()` is a standalone call. Router invokes it via `_maybe_resolve_cycle()` hook. No RA logic inlined in router. |
| **G11: No Cycle Start Prompt** | None | RA summary feeds into next DM prompt | `prompts.py` adds `_inject_ra_summary()` and `_inject_base_rules()` helpers. `build_system_prompt()` calls them via 2-line hooks. |

**Impact:** High. Core orchestration logic changes.

### 3.3 Prompts (`core/prompts.py`)

| Gap | Current | Target | Change |
|-----|---------|--------|--------|
| **G12: No BASE_RULES** | Rules scattered in system prompt | Shared `BASE_RULES` constant for both agents | Extract meta-mechanics into `BASE_RULES` constant |
| **G13: No RA System Prompt** | None | RA needs its own system prompt | Add `build_ra_system_prompt()` |
| **G14: No Cycle Context in DM Prompt** | DM sees `memory_summary` (free-text) | DM sees last RA summary (structured) + cycle start prompt | Modify `build_system_prompt()` to include RA output |
| **G15: No Cycle Start Prompt Builder** | None | Framework generates transition prompt | Add `build_cycle_start_prompt(ra_summary)` |

**Impact:** Medium-High. Prompt engineering; behavior-critical.

### 3.4 Main Entry (`main.py`)

| Gap | Current | Target | Change |
|-----|---------|--------|--------|
| **G16: No Cycle State Hook** | `_handle_dm_event()` processes all messages | Must check cycle state before routing to DM | Add cycle state gate: if resolving/transition, handle appropriately |
| **G17: No RA Lifecycle Integration** | Heartbeat only checks turns | No RA scheduling needed (RA is synchronous) | RA runs inline after cycle end; no async scheduling needed |
| **G18: Cycle End Signal Path** | None | DM can signal cycle end via tool or completion text | Add `cycle_control` tool or detect signal in completion |

**Impact:** Medium. Hook points only; core fast paths unchanged.

### 3.5 Tool Registry (`tools/registry.py`)

| Gap | Current | Target | Change |
|-----|---------|--------|--------|
| **G19: No Cycle Control Tool** | None | DM needs to signal cycle end | Add `cycle_control` tool (or overload `turn_control` / `session_control`) |
| **G20: RA Tool Access** | All tools available to DM | RA has NO tool access | Ensure `RecorderAgent` does not receive tool registry |

**Impact:** Low. Add one tool; no existing tool changes.

### 3.6 Turn System (`tools/turn_tools.py`)

| Gap | Current | Target | Change |
|-----|---------|--------|--------|
| **G21: Turn vs Cycle Boundary** | Turns are combat mechanical | Cycles are narrative; one cycle spans multiple turns | Turn system stays unchanged; cycle boundary is DM decision, not turn mechanic |

**Impact:** None. Turn system unchanged by design.

### 3.7 Memory Compressor (`core/memory.py`)

| Gap | Current | Target | Change |
|-----|---------|--------|--------|
| **G22: MemoryCompressor vs RA** | Compresses to free-text `memory_summary` | RA provides structured summaries | Option A: Replace MemoryCompressor with RA summaries. Option B: Use RA summaries as higher-fidelity input to MemoryCompressor. |

**Impact:** Medium. Design decision needed (see Open Item #2 in design doc).

### 3.8 Storage (`storage/json_repository.py`)

| Gap | Current | Target | Change |
|-----|---------|--------|--------|
| **G23: No RA Audit Trail** | Audit logs all DM actions | Need to audit RA runs too | Append RA execution to audit log |

**Impact:** Low. One-line addition.

### 3.9 Configuration (`_conf_schema.json`)

| Gap | Current | Target | Change |
|-----|---------|--------|--------|
| **G24: No RA Config** | Only `enabled_sessions`, `trigger_prefixes`, `allow_private_chat` | Need RA model config, enable/disable toggle, cost controls | Add `ra_model_provider`, `ra_enabled`, `ra_max_tokens` |

**Impact:** Low. Config schema addition.

---

## 4. Implementation File Map

### 4.1 New Files

| File | Lines (est.) | Description |
|------|-------------|-------------|
| `core/environment_agent.py` | ~150 | RecorderAgent class: `run_cycle_resolution(session, ra_cycle_input) -> RASummary` |
| `core/cycle_state_machine.py` | ~80 | `CycleStateMachine`: state transitions, validation, cycle_start_prompt generation |

### 4.2 Files to Modify

| File | Changes |
|------|---------|
| `core/models.py` | Add `CycleState`, `AuditBuffer`, `RACycleInput`, `CycleAction`, fields to `GameSession` |
| `core/router.py` | **Minimal hooks only**: add `_maybe_append_cycle_buffers()` and `_maybe_resolve_cycle()` private methods (3-5 lines each) that delegate to `CycleStateMachine` / `RecorderAgent`. Main `handle_message()` inserts 2 one-line calls. No inline cycle logic. |
| `core/prompts.py` | **Minimal hooks only**: add `BASE_RULES`, `build_ra_system_prompt()`, `build_cycle_start_prompt()`, `_inject_base_rules()`, `_inject_ra_summary()`. `build_system_prompt()` inserts 2 one-line calls. No inline prompt assembly. |
| `main.py` | **Minimal gate only**: add `_cycle_state_gate()` private method. `_handle_dm_event()` inserts 1 guard clause (3 lines). No inline state logic. |
| `tools/registry.py` | Add `cycle_control` tool |
| `storage/json_repository.py` | Add RA audit logging |
| `_conf_schema.json` | Add RA configuration fields |

### 4.3 Files Unchanged

| File | Reason |
|------|--------|
| `tools/turn_tools.py` | Cycles are narrative, turns are combat; no overlap |
| `spatial/engine.py` | Spatial engine is deterministic; RA reads its outputs, never calls it |
| `rules/python_runtime.py` | RA reads rule execution results, never registers/executes rules |
| `tools/memory_tools.py` | DM still owns all state mutations via tools |
| `core/security.py` | Security precheck applies before DM; RA sees no player input |

---

## 5. Critical Design Decisions for Implementation

### D1: How does DM signal cycle end?

**Option A — Explicit tool:** DM calls `cycle_control(action="end_cycle")`. Most reliable.
**Option B — Completion text signal:** DM outputs `"[CYCLE_END]"` or similar. Fragile.
**Option C — Framework heuristic:** Detect when all players have acted + turn phase is idle. Too rigid; contradicts "cycle is narrative" principle.

**Recommendation: Option A.** Add `cycle_control` as a tool alias or standalone tool. DM decides narrative boundaries.

### D2: Does RA run in the same LLM provider?

**Option A — Same provider, same model:** Simplest; no infra changes.
**Option B — Same provider, cheaper model:** Cost savings; requires provider support for model selection per call.
**Option C — Different provider entirely:** Most flexible; requires abstracting LLM call interface.

**Recommendation: Option A for MVP; Option B for v2.** The current `astr_context.llm_generate()` path is provider-agnostic enough that Option B is a config change.

### D3: What happens when RA fails?

Per design doc: "RA failures must not block the game. Invalid JSON → skip and continue."

Implementation: Wrap RA run in try/except. On failure:
1. Log error to audit; increment `session._ra_failure_count`
2. **Preserve unconsumed `audit_buffer`** -- do NOT clear it
3. Log recoverable error to `session._ra_recovery_log`
4. Generate **minimal state patch** from last known authoritative tool state (update only fields confirmed by tool traces)
5. Transition `CYCLE_RESOLVING → CYCLE_ACTIVE` directly (skip CYCLE_TRANSITION)
6. DM continues with patched state; next successful RA must process the preserved buffer

### D4: Where does the Audit Buffer live?

Must be in `GameSession` (in-memory + persisted), not a separate global buffer.
Reason: Sessions can be reloaded from disk; cycle must survive plugin restart.

### D5: How does RA reconcile DM narrative with tool trace?

**Golden Rule**: RA distinguishes "narrative fields" from "authority structured fields".

- **Narrative fields** (scene description, atmosphere, NPC dialogue) -- RA follows DM narration. DM has final interpretive authority.
- **Authority structured fields** (HP, MP, alive, position, inventory, conditions, etc.) -- RA MUST base these on tool trace, validator results, and state transition outcomes. If DM says "goblin falls" but `execute_rule` returns 4 HP remaining, RA records `hp=4, alive=true` and logs the conflict to `discrepancies`.

**DM narrative reconciliation**: When the DM Agent finds non-empty `discrepancies` in the next cycle, it MUST reconcile the conflict through plausible in-narrative explanations (e.g., "stumbling", "knocked back", "playing dead"). If the discrepancy cannot be rationalized, the DM should briefly correct the previous narrative so that player-visible narrative ultimately aligns with the authoritative structured state.

This contract is the core boundary between the two agents. Violating it (RA overriding DM narration, or DM ignoring tool-based state) breaks the architecture.

### D6: How do we minimize merge conflicts with concurrent features?

**Problem**: Other PRs (e.g., external memory integrations like Honcho) modify the same files (`main.py`, `core/router.py`, `core/prompts.py`). Heavy inline changes create merge conflicts.

**Solution — Modular Hook Architecture**:
- **Existing files get minimal one-line hooks only**. No inline business logic.
- **New code lives in new files or isolated private methods**.

Concrete pattern:
| File | Hook |
|------|------|
| `main.py` | `_handle_dm_event()` inserts `if self._cycle_state_gate(session): return wait_msg` (1 guard clause) |
| `core/router.py` | `handle_message()` inserts `_maybe_append_cycle_buffers(session, result)` and `_maybe_resolve_cycle(session)` (2 one-line calls) |
| `core/prompts.py` | `build_system_prompt()` inserts `_inject_base_rules(prompt)` and `_inject_ra_summary(prompt, session)` (2 one-line calls) |

All complex logic (buffer generation, projection redaction, RA invocation, state transitions) lives in `core/cycle_state_machine.py` and `core/environment_agent.py`.

**Benefits**:
- Merge conflicts are reduced to isolated one-line additions.
- Feature flags (`ra_enabled`) naturally short-circuit at the hook boundary.
- Future features can add their own hooks without touching existing dual-agent code.

---

## 6. Verification Checklist

After implementation, the following must hold:

- [ ] `GameSession` serializes/deserializes with new cycle fields without data loss
- [ ] DM can call `cycle_control` to end a cycle
- [ ] After cycle end, RA runs exactly once
- [ ] RA output is valid JSON matching the cycle summary schema
- [ ] RA output is saved to session state
- [ ] DM's next system prompt includes the RA summary + any non-empty `discrepancies`
- [ ] DM Agent handles `discrepancies` through plausible in-narrative reconciliation in the next cycle
- [ ] If RA fails, game continues without blocking; `audit_buffer` is preserved for retry
- [ ] Failed RA generates minimal state patch from last known authoritative tool state
- [ ] Turn system (combat rounds) continues to work independently
- [ ] Local fast paths (pause, status, token) work during any cycle state
- [ ] Audit log contains both DM tool steps and RA execution records
- [ ] RA input allowlist enforced: no `player_message`, PII, or diagnostic fields leaked to RA
- [ ] MemoryCompressor still functions (if not replaced by RA)

---

## 7. Estimated Effort

| Phase | Scope | Est. Time |
|-------|-------|-----------|
| Phase 1: Data Model + Cycle State Machine | `models.py`, `cycle_state_machine.py`, `prompts.py` BASE_RULES | 1 day |
| Phase 2: Router Integration + Audit Buffer | `router.py` cycle append/detection, `main.py` gate | 1 day |
| Phase 3: Recorder Agent | `environment_agent.py`, RA prompts, RA LLM integration | 1-2 days |
| Phase 4: Cycle End Tool + Testing | `tools/registry.py` cycle_control, pytest coverage | 1 day |
| Phase 5: End-to-End Integration | Full pipeline test, edge cases (RA failure, cycle during combat) | 1-2 days |

**Total: 5-7 days for a single developer.**

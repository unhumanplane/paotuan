# Design Document: Dual-Agent TRPG Architecture

## Overview

This document defines the architecture for splitting the current monolithic TRPG DM into two distinct agents with clear separation of responsibilities, operating across three game lifecycle scenarios.

## Agent Definitions

### DM Agent (Creative Director)
- **Sees**: Full game state, player input, BASE_RULES, all prior RA summaries
- **Owns**: Narrative, atmosphere, dialogue, story direction, player intent interpretation
- **Acts**: Calls all tools, defines game-specific rules, decides when cycles start/end
- **Output**: Narrative text directed at players + tool calls that mutate game state
- **Does NOT do**: Structured bookkeeping, stat computation, machine-readable state diffs

### RA -- Recorder Agent (State Normalizer)
- **Sees**: `ra_cycle_input` filtered projection + sanitized authority-field snapshot + BASE_RULES. RA never receives the full `GameSession`.
  - **RA input allowlist**: `dm_narrative`, sanitized `tools_called` results, `character_status` authority fields, `scene` public fields, `world_tags`, `rule_sets`, `BASE_RULES`
  - **RA input blocklist** (default excluded): `player_message`, raw player input, hidden DM notes, system prompt, diagnostic fields (token usage, debug logs), PII (display names, etc.)
- **Owns**: Structured data generation, stat normalization, consistency recording, cycle summaries
- **Acts**: Reads filtered DM output and tool results, produces structured JSON, and lets the framework validate/apply allowlisted patches
- **Output**: Machine-readable structured JSON (never sent directly to players)
- **Does NOT do**: Creative decisions, narrative generation, tool calls, overriding DM narration

**Golden Rule**: RA distinguishes "narrative fields" from "authority structured fields".
- **Narrative fields** (scene description, atmosphere, NPC reactions, dialogue) -- follow DM narration; DM has final interpretive authority.
- **Authority structured fields** (HP, MP, alive, position, inventory, conditions, quest state, resource counts, etc.) -- **MUST be based on tool trace, validator results, and state transition outcomes**. If DM says "goblin falls" but `execute_rule` returns 4 HP remaining, then `hp=4, alive=true`, while `discrepancies` records: `"DM narrative describes enemy falling, but tool resolution shows hp=4"`.
- RA does **not** override authoritative values from tool traces, but **preserves** DM narrative text as scene description.
- **Narrative reconciliation responsibility**: When the DM Agent finds non-empty `discrepancies` in the next cycle, it MUST reconcile the conflict through plausible in-narrative explanations. For example, "falling" can be explained as stumbling, being knocked back, losing balance, or playing dead. If the discrepancy cannot be rationalized, the DM should briefly correct the previous narrative to ensure player-visible narrative ultimately aligns with the authoritative structured state.

## Shared Constants

### BASE_RULES
A constant document injected into both agents' system prompts. Contains:
- Meta-mechanics (e.g., "this game uses d20 dice system")
- Prohibited behaviors (no OOC, no meta-gaming)
- Universal constraints (no PvP without consent, no plot rewrites after start_game)

Game-specific rules are defined by the DM Agent during play and captured through DM/tool outputs. RA may propose structured `rule_sets` patches from that evidence; the framework validates those patches before saving them to session data and feeding them back to DM Agent on subsequent turns.

## Game Lifecycle & Scenarios

### Scenario 1: Game Initialization (Prepare Phase)

```
Player: /dm init
  |
DM Agent -- proposes 3-5 background options (cyberpunk, magic&sword, anime, etc.)
  |
[Players discuss in normal chat -- INVISIBLE to both agents]
  |
Player: /dm choose #1
  |
DM Agent -- confirms the explicitly selected option
  |
DM Agent -- passes narrative background to RA
  |
RA -- proposes validated initialization JSON: {story_background, major_tasks, genre_rules, ...}
  |
[Framework applies allowlisted patch to session.world_tags / session.scene]
  |
DM Agent -- asks players to describe characters
  |
Player: /dm I want to be a frail old wizard
  |
DM Agent -- rewrites/flavors description, passes to RA
  |
RA -- proposes a structured character card patch:
    {name, class, hp, mp, stats, tags, ...}
  |
[Framework validates and applies allowlisted patch to session.characters]
  |
Once all characters ready -- DM Agent calls start_game -- game begins
```

**Key pattern**: DM Agent handles all player-facing negotiation and creativity. RA handles all structured output generation. DM passes narrative; RA returns JSON.

### Scenario 2: Game Cycle (Active Play)

#### Per-Player Action Flow

```
Player John: /dm which enemy is easier to kill?
  |
DM Agent -- checks state, answers directly
  |
[NO RA INVOLVED -- this is a query, not an action]

---

Player John: /dm rush and kill enemy B
  |
DM Agent -- calls tools (check_attack_vector, execute_rule, turn_control)
  |
DM Agent -- narrates result to player
  |
Action result + tool trace -- appended to audit_buffer (full) + ra_cycle_input (filtered, sanitized projection)
  |
[NO RA LLM CALL YET -- just data accumulation]
```

#### Cycle-End Flow

```
After last player acts:
  |
DM Agent decides the narrative cycle is complete
  |
DM Agent calls `cycle_control(action="end_cycle")`
  |
Framework triggers RA (ONE LLM CALL)
  |
RA reads:
  - `ra_cycle_input` projection (filtered action results: dm_narrative + tools_called)
  - Previous master state (authority field snapshot)
  - BASE_RULES
  |
RA produces:
  - Cycle summary JSON
  - Allowlisted authority-field patch candidates for characters, enemies, scene, and rule sets
  - Discrepancy log comparing DM narration against tool/validator outcomes
  |
[Framework saves summary to session.environment_summaries and applies only validated, tool-backed patches to session.characters / session.scene]
  |
Framework State Machine transitions: CYCLE_RESOLVING -> CYCLE_TRANSITION
  |
Framework generates "Cycle Start Prompt" for DM Agent from the validated RA summary
  |
DM Agent receives:
  - Cycle summary from RA
  - Cycle Start Prompt
  - Full game state
  |
DM Agent pushes story forward -- "The wounded orc flees into the mist..."
  |
Cycle #2 begins
```

**Key pattern**: RA LLM runs **only at cycle end**, not per-action. Individual action results are buffered. This keeps token costs bounded and latency acceptable for players.

### Scenario 3: Game End (Post-Game)

```
Major story arc complete OR DM/admin explicitly ends the game
  |
DM Agent -- provides final narrative result
  |
DM passes to RA -- RA structures final outcome as normal cycle
  |
Framework passes WHOLE story line + prompt to DM Agent
  |
DM Agent -- generates campaign retrospective summary
  |
RA -- reads all game cycle data -- produces statistics:
  - Total damage dealt per character
  - Enemies defeated
  - Key player choices
  - Survival rate
  - Rules used
  - Plot branches taken
  |
[All saved for posterity / replay / leaderboard]
```

## Cycle State Machine

A higher-level state machine sits above the existing `GameMode` and `turn_control` systems:

```
CYCLE_ACTIVE
  |-- Players take actions (DM resolves, results buffered)
  |-- DM may advance combat rounds via existing turn_control
  |-- Queries answered directly by DM
  |-- DM calls `cycle_control(action="end_cycle")`
      |
CYCLE_RESOLVING
  |-- RA runs (one LLM call)
  |-- RA generates summary + allowlisted patch candidates
      |
CYCLE_TRANSITION
  |-- Framework generates Cycle Start Prompt
  |-- DM Agent receives prompt + validated state
  |-- DM Agent narrates transition / next scene
      |
CYCLE_ACTIVE (next cycle)
```

**Note**: `CYCLE_ACTIVE` may span multiple combat rounds (existing `turn_control` rounds). The cycle boundary is a **narrative** decision by the DM Agent, not a combat mechanic.

## Data Structures

### Audit Buffer (audit_buffer)
Stored in session state, cleared at cycle start, appended per action, used for audit and debugging:

```json
{
  "cycle_id": 1,
  "actions": [
    {
      "player_id": "john",
      "character_id": "pc_john",
      "player_message": "rush and kill enemy B",
      "dm_narrative": "John charges...",
      "tools_called": [
        {"name": "execute_rule", "args": {...}, "result": {...}}
      ],
      "timestamp": "..."
    }
  ],
  "started_at": "...",
  "ended_at": "..."
}
```

### RA Input Projection (ra_cycle_input)
Framework-generated filtered input for RA, **excluding** `player_message`:

```json
{
  "cycle_id": 1,
  "actions": [
    {
      "dm_narrative": "John charges...",
      "tools_called": [
        {"name": "execute_rule", "args_sanitized": {...}, "result_sanitized": {...}}
      ]
    }
  ]
}
```

**Allowlist rule**: RA input contains only `dm_narrative`, sanitized `tools_called`, previous cycle's `character_status` / `enemy_status` authority fields, `world_tags`, `rule_sets`, and `BASE_RULES`. Tool arguments/results must be projected before RA sees them; no PII, diagnostic fields, raw player input, prompts, or raw audit payloads may be passed to RA.

### RA Output -- Cycle Summary + Patch Candidates

```json
{
  "cycle_id": 1,
  "summary": "John wounded Orc_B. Alice healed John. The party advanced.",
  "character_status": {
    "pc_john": {"hp": 14, "mp": 8, "conditions": [], "position": [3, 4]}
  },
  "enemy_status": {
    "orc_b": {"hp": 4, "conditions": ["wounded"], "alive": true}
  },
  "world_changes": {
    "new_entities": [],
    "scene_updates": {"current_conflict": "Orc_B retreating"}
  },
  "rules_triggered": ["melee_attack", "heal_light"],
  "dm_narrative_aligned": true,
  "discrepancies": []
}
```

`character_status`, `enemy_status`, and `world_changes` are patch candidates. The framework applies them only if every field is allowlisted and backed by tool traces or validator outcomes.

### RA Output -- Game Statistics (End Game)

```json
{
  "total_cycles": 12,
  "characters": {
    "pc_john": {"total_damage_dealt": 147, "enemies_defeated": 3, "times_down": 1}
  },
  "plot_branches": ["spared_the_witch", "burned_the_bridge"],
  "rules_used": {"melee_attack": 24, "fireball": 8},
  "survival_rate": "100%"
}
```

## Pipeline Order (Confirmed)

**Sequential, synchronous: DM first, then RA.**

```
Player /dm message
  |
DM Agent runs (multi-hop tool loop)
  |
DM response yielded to player (player sees this immediately)
  |
Action result + tool trace -> audit_buffer (full data) -> ra_cycle_input (filtered projection)
  |
[No RA LLM call yet for individual actions]
  |
... more player actions ...
  |
DM calls `cycle_control(action="end_cycle")`
  |
RA runs ONCE (reads ra_cycle_input + sanitized authority snapshot)
  |
Framework saves RA summary and applies validated patch candidates
  |
State machine transitions
  |
DM Agent receives Cycle Start Prompt for next cycle
```

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| RA does not see player input | Prevents RA from second-guessing DM's interpretation of player intent |
| RA runs once per cycle, not per action | Token cost control; per-action LLM calls would be prohibitively expensive |
| Cycle is narrative, not combat | Existing `turn_control` handles combat rounds; cycles handle story beats |
| BASE_RULES as shared constant | Both agents agree on meta-mechanics; DM defines specifics per-campaign |
| Framework saves RA summary and applies validated RA patches | DM Agent reads the summary via system prompt on next cycle; authority fields only change through allowlisted, tool-backed patches |
| RA does not override authority fields | Narrative follows DM; HP/position/alive/etc. follow tool trace; conflicts logged to discrepancies |
| audit_buffer stores full action data; ra_cycle_input is filtered projection | RA needs cycle context but must not access player_message / PII / diagnostics / prompts / raw audit payloads |

## Open Items (Post-Design)

1. **Vote tool**: Future work, not in initial scope.
2. **MemoryCompressor integration**: Can use RA summaries as higher-fidelity compression input.
3. **Cheaper LLM for RA**: Same provider initially; future config for separate model.
4. **Error handling**: RA failures must not block the game, but must not silently lose resolved state.
   - Invalid JSON / timeout / exception: preserve unconsumed `audit_buffer`, log recoverable error to `session._ra_recovery_log`
   - Use last known authoritative tool state to generate a minimal state patch only through the same allowlist/validator path used for RA patches
   - `cycle_state` returns directly to `CYCLE_ACTIVE` (skip `CYCLE_TRANSITION`)
   - Current cycle's `audit_buffer` must not be cleared until a successful RA run completes

## Files Referenced

| File | Role |
|------|------|
| `astrbot_plugin_auto_trpg_dm/main.py` | Plugin entry, message routing, cycle state machine hook |
| `astrbot_plugin_auto_trpg_dm/core/router.py` | DM Agent orchestration, tool loop |
| `astrbot_plugin_auto_trpg_dm/core/models.py` | GameSession, audit_buffer, ra_cycle_input, RA output fields |
| `astrbot_plugin_auto_trpg_dm/core/prompts.py` | DM system prompt, RA system prompt, BASE_RULES |
| `astrbot_plugin_auto_trpg_dm/core/environment_agent.py` | RA implementation (NEW) |
| `astrbot_plugin_auto_trpg_dm/tools/registry.py` | Tool whitelist (DM only; RA has no tool access) |
| `astrbot_plugin_auto_trpg_dm/tools/turn_tools.py` | Combat turn system (unchanged, nested inside cycles) |

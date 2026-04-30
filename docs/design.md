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
- **Sees**: DM Agent output ONLY + tool trace + BASE_RULES + current session snapshot
- **Does NOT see**: Player input directly, hidden DM notes, system prompt
- **Owns**: Structured data generation, stat normalization, consistency recording, cycle summaries
- **Acts**: Reads DM output and tool results, produces structured JSON, saves to session state
- **Output**: Machine-readable structured JSON (never sent directly to players)
- **Does NOT do**: Creative decisions, narrative generation, tool calls, overriding DM narration

**Golden Rule**: RA strictly follows DM narration. If DM says "goblin falls" but tool trace shows 4 HP remaining, RA records what the DM narrated and may note the discrepancy, but does not override.

## Shared Constants

### BASE_RULES
A constant document injected into both agents' system prompts. Contains:
- Meta-mechanics (e.g., "this game uses d20 dice system")
- Prohibited behaviors (no OOC, no meta-gaming)
- Universal constraints (no PvP without consent, no plot rewrites after start_game)

Game-specific rules are defined by the DM Agent during play, passed to RA for structuring into `rule_sets`, saved to session data, and fed back to DM Agent on subsequent turns.

## Game Lifecycle & Scenarios

### Scenario 1: Game Initialization (Prepare Phase)

```
Player: /dm init
  |
DM Agent -- proposes 3-5 background options (cyberpunk, magic&sword, anime, etc.)
  |
[Players discuss in normal chat -- INVISIBLE to both agents]
  |
Player: /dm vote #1
  |
DM Agent -- collects votes, ranks, confirms winner
  |
DM Agent -- passes narrative background to RA
  |
RA -- structures into JSON: {story_background, major_tasks, genre_rules, ...}
  |
[Saved to session.world_tags / session.scene]
  |
DM Agent -- asks players to describe characters
  |
Player: /dm I want to be a frail old wizard
  |
DM Agent -- rewrites/flavors description, passes to RA
  |
RA -- normalizes into structured character card:
    {name, class, hp, mp, stats, tags, ...}
  |
[Saved to session.characters]
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
Action result + tool trace -- appended to Cycle Buffer
  |
[NO RA LLM CALL YET -- just data accumulation]
```

#### Cycle-End Flow

```
After last player acts:
  |
DM Agent detects cycle complete (via turn state or explicit reasoning)
  |
DM Agent signals: "cycle complete"
  |
Framework triggers RA (ONE LLM CALL)
  |
RA reads:
  - Cycle Buffer (all action results from this cycle)
  - Tool traces
  - Previous master state
  |
RA produces:
  - Updated master structured state
  - Cycle summary JSON
  - Character status tables
  - Enemy status tables
  - World changes log
  |
[Saved to session.environment_summaries / session.characters / session.scene]
  |
Framework State Machine transitions: CYCLE_RESOLVED -> CYCLE_START
  |
RA generates "Cycle Start Prompt" for DM Agent
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
Major story arc complete OR Player: /dm end [vote passes]
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
  |-- DM detects cycle complete -- signals
      |
CYCLE_RESOLVING
  |-- RA runs (one LLM call)
  |-- RA updates master state, generates summary
      |
CYCLE_TRANSITION
  |-- Framework generates Cycle Start Prompt
  |-- DM Agent receives prompt + updated state
  |-- DM Agent narrates transition / next scene
      |
CYCLE_ACTIVE (next cycle)
```

**Note**: `CYCLE_ACTIVE` may span multiple combat rounds (existing `turn_control` rounds). The cycle boundary is a **narrative** decision by the DM Agent, not a combat mechanic.

## Data Structures

### Cycle Buffer
Stored in session state, cleared at cycle start, appended per action, consumed by RA at cycle end:

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

**Important**: The RA does NOT see `player_message` directly. The DM Agent's narrative (`dm_narrative`) and `tools_called` are the RA's inputs. The `player_message` is stored for audit/debug only.

### RA Output -- Cycle Summary

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
Action result + tool trace -> Cycle Buffer (if action)
  |
[No RA call yet for individual actions]
  |
... more player actions ...
  |
DM signals cycle end
  |
RA runs ONCE (reads Cycle Buffer + state)
  |
RA saves structured output
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
| RA output saved to session state | DM Agent reads it via system prompt on next cycle |
| RA never overrides DM | DM is the creative authority; RA is the scribe |
| Cycle Buffer stores action data | RA needs complete cycle context to produce accurate summary |

## Open Items (Post-Design)

1. **Vote tool**: Future work, not in initial scope.
2. **MemoryCompressor integration**: Can use RA summaries as higher-fidelity compression input.
3. **Cheaper LLM for RA**: Same provider initially; future config for separate model.
4. **Error handling**: RA failures must not block the game. Invalid JSON -> skip and continue.

## Files Referenced

| File | Role |
|------|------|
| `astrbot_plugin_auto_trpg_dm/main.py` | Plugin entry, message routing, cycle state machine hook |
| `astrbot_plugin_auto_trpg_dm/core/router.py` | DM Agent orchestration, tool loop |
| `astrbot_plugin_auto_trpg_dm/core/models.py` | GameSession, Cycle Buffer, RA output fields |
| `astrbot_plugin_auto_trpg_dm/core/prompts.py` | DM system prompt, RA system prompt, BASE_RULES |
| `astrbot_plugin_auto_trpg_dm/core/environment_agent.py` | RA implementation (NEW) |
| `astrbot_plugin_auto_trpg_dm/tools/registry.py` | Tool whitelist (DM only; RA has no tool access) |
| `astrbot_plugin_auto_trpg_dm/tools/turn_tools.py` | Combat turn system (unchanged, nested inside cycles) |

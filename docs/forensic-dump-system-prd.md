# Forensic Dump System PRD

## Objective

Build a non-invasive, opt-in forensic dump system that captures the complete causal chain of every DM turn — state, prompts, raw LLM responses, tool executions, guard decisions, and state mutations — so that developers and analysts can reconstruct exactly what the agent saw, why it made a decision, and what changed as a result.

## Background

Paotuan already has strong observability:

- Full `GameSession` JSON saves per turn (`saves/<session_id>.json`).
- JSONL audit logs (`audit/<session_id>.jsonl`) with dozens of event types.
- Automatic save backups with rotation.
- Structured plugin logs (`auto_trpg_dm.log`).
- Admin web dashboard with snapshot/audit endpoints.

**What is missing:**
- Exact prompt text (only hashes and char counts are logged).
- Raw LLM response objects (only `completion_text` and extracted tool calls are kept).
- A per-turn self-contained artifact tying state-before, cognition, action, and state-after together.
- A single export that packages a session’s save + audit + logs + dumps for offline analysis.

## Design Principles

1. **Non-invasive** — When disabled, zero overhead and zero behavioral change.
2. **Boundary capture** — Hook at the edges (LLM input/output, tool I/O) rather than instrumenting deep business logic.
3. **Separability** — Dump data lives outside the hot-path session save so it does not slow down loads or bloat primary game state.
4. **Plain text by default** — Dump files are plain JSON so LLMs and humans can read them directly without decompression.
5. **Backward compatible** — No breaking changes to `GameSession`, existing audit logs, or save format.

## Data Model: Turn Envelope

Every player turn (or system turn such as RA cycle resolution) produces exactly one **Turn Envelope** — a self-contained JSON file.

### Envelope Schema (v1)

```json
{
  "envelope_version": "1.0",
  "turn_id": "<uuid>",
  "session_id": "...",
  "cycle_id": 42,
  "turn_sequence": 5,
  "timings": {
    "start": "2026-05-19T15:42:30+08:00",
    "end": "2026-05-19T15:42:45+08:00",
    "llm_total_ms": 3200
  },
  "actor": {
    "player_id": "...",
    "display_name": "..."
  },
  "player_message": "...",
  "routing": {
    "mode": "tactical",
    "provider_id": "openai",
    "security_notes": [],
    "fast_path_triggered": false,
    "fast_path_action": null
  },
  "state": {
    "before": { /* full session.to_dict() at turn start */ },
    "after": { /* full session.to_dict() at turn end */ },
    "diff": { /* semantic diff, see State Diff section */ }
  },
  "prompts": {
    "system_prompt": "...",
    "user_prompt": "...",
    "tool_names": ["resolve_check", "update_scene"],
    "tool_specs": [...],
    "projection_stats": {...},
    "component_chars": {...}
  },
  "llm_interactions": [
    {
      "step": 1,
      "request": {
        "prompt": "...",
        "contexts": [...],
        "system_prompt": "..."
      },
      "response": {
        "completion_text": "...",
        "tool_calls": [{"name": "...", "args": {}}],
        "raw_response_safe": {...},
        "usage": {...},
        "finish_reason": "..."
      },
      "tool_executions": [
        {
          "tool": "resolve_check",
          "args": {},
          "result": {},
          "guard_blocked": false,
          "guard_reason": null
        }
      ]
    }
  ],
  "guards_fired": [
    {
      "name": "adjudication_completeness_guard",
      "decision": "applied_suffix",
      "metadata": {}
    }
  ],
  "post_processing": {
    "completion_limited": {"from_chars": 800, "to_chars": 700},
    "menu_cleanup": {"changed": true, "reason": "...", "removed_blocks": 2},
    "continuity_audit": {"ok": true, "issues": 1, "applied": 1},
    "deterministic_repair": {"applied": 0, "rejected": 0}
  },
  "final_output": {
    "completion_text": "...",
    "dice_summary": "",
    "pending_outputs": [],
    "sent_to_player": true
  },
  "metadata": {
    "envelope_size_bytes": 152340,
    "prompt_hashes": {"system": "abc123", "user": "def456"}
  }
}
```

### Field Notes

- `raw_response_safe` — A JSON-safe dict extracted from the provider response object. Captures `completion_text`, `tool_calls`, `usage`, `model`, `finish_reason`, and provider-specific reasoning/thinking fields if present. The actual provider object is discarded after extraction.
- Fast-path turns (pause, resume, duplicate rejection, security block) produce lightweight envelopes with empty `llm_interactions` but still include `state.before/after`, `routing.fast_path_triggered`, and `final_output`.

## Storage Strategy

### Physical Layout

```
data/plugin_data/astrbot_plugin_auto_trpg_dm/
├── saves/
├── audit/
├── save_backups/
└── dumps/                          <-- NEW
    └── <session_id>/
        ├── 20260519_154230_001_a7f3.json
        ├── 20260519_154245_002_b2e1.json
        └── ...
```

- One folder per session.
- One plain JSON file per turn.
- File naming: `<YYYYMMDD>_<HHMMSS>_<sequence>_<hash>.json`

### Why Plain JSON (No Gzip)

Disk savings from gzip are real (~80–90% for text-heavy JSON), but the primary consumers of dumps are LLMs and human analysts. Requiring decompression adds friction that outweighs disk cost. If disk pressure becomes a problem later, a background rotation task can compress dumps older than N days — but the hot, analyzable data stays readable as-is.

### Write Behavior

- Envelope assembly happens synchronously at the end of the turn.
- File I/O is dispatched via `asyncio.create_task()` so the player reply is never blocked.
- `JsonGameRepository` handles write and rotation.

### Rotation Policy (Config-Driven)

| Key | Default | Description |
|---|---|---|
| `forensic_dumps_enabled` | `false` | Master switch. |
| `forensic_max_turns_per_session` | `500` | Delete oldest dumps when exceeded. |
| `forensic_retain_days` | `30` | Delete dumps older than N days on write. |
| `forensic_include_prompts` | `false` | Include full prompt text in envelope. |
| `forensic_include_raw_response` | `false` | Include `raw_response_safe` in envelope. |

## Capture Architecture

We instrument at five boundaries inside the existing call chain. A `ForensicCollector` instance is created at the entry point and passed **optionally** down the stack.

```
_handle_dm_event()
├── [CAPTURE-1] ENTRY: state_before, actor, message, security_notes
├── _local_fast_path() ?
│   └── [CAPTURE-2] FAST_PATH: record action & skip LLM trace
└── router.handle_message(collector=collector)
    └── _handle_message_once(collector=collector)
        ├── [CAPTURE-3] PROMPT: system_prompt, user_prompt, specs, projection_stats
        ├── _run_llm_tool_loop(collector=collector)
        │   ├── loop step N
        │   │   ├── [CAPTURE-4a] LLM_REQUEST: prompt, contexts, system_prompt
        │   │   ├── _llm_generate() -> raw response
        │   │   ├── [CAPTURE-4b] LLM_RESPONSE: raw_response_safe, completion_text, tool_calls, usage
        │   │   ├── tool_executor.execute()
        │   │   └── [CAPTURE-4c] TOOL_EXEC: name, args, result, guard_block
        │   └── ...
        ├── [CAPTURE-5] GUARDS: completeness, menu cleanup, continuity audit, deterministic repair, auto-advance
        └── [CAPTURE-6] EXIT: state_after, compute diff, build envelope, schedule write
```

If `collector` is `None`, every capture point is a no-op.

## State Diff

We do **not** use a naive text diff on two JSON blobs. Instead, `compute_session_diff(before, after)` produces a semantic report focused on what a DM analyst cares about:

```json
{
  "characters": {
    "alice": {
      "tags_added": [{"key": "prone", "layer": "status"}],
      "tags_modified": [{"key": "hp", "old": 12, "new": 8}],
      "tags_removed": []
    }
  },
  "scene": {
    "keys_changed": {
      "summary": {"old": "...", "new": "..."},
      "current_conflict": {"old": "...", "new": "..."}
    },
    "keys_added": {},
    "keys_removed": []
  },
  "battle": {
    "turn_changed": true,
    "round": {"old": 2, "new": 3},
    "current_entity_id": {"old": "goblin_1", "new": "alice"}
  },
  "timeline": {"events_added": 1},
  "maps": {
    "active_strict_map_id_changed": false,
    "records_modified": ["strict-local-map"]
  },
  "rules": {"registered": 0},
  "memory_summary_changed": true
}
```

Algorithm:
- Recursive walk over both dicts.
- Lists are compared by identity/index for ordered structures (e.g. `turn_order`) or by element hash for set-like structures (e.g. tags).
- String values beyond 240 chars are summarized as `"{old_len}→{new_len} chars"` to keep the diff readable.
- Performance: two dict walks on a ~500 KB session object; negligible compared to LLM latency.

## Export & Archive

### In-Chat Export

Extend `session_control` tool with a new action:

- `export_forensic` / `导出取证`
- Behavior: Calls `repository.archive_session_forensic()` to create a ZIP.
- Returns: ZIP file path and turn count.
- If dumps are disabled, returns an error telling the user to enable `forensic_dumps_enabled`.

### ZIP Contents

```
<session_id>_forensic_<timestamp>.zip
├── session_save.json
├── audit.jsonl
├── plugin_log_tail.txt          (last 5,000 lines)
├── backups_manifest.json
├── dumps/
│   ├── 20260519_154230_001_a7f3.json
│   └── ...
└── README.txt                   (turn count, session duration, dump version)
```

### Admin Web Endpoints

Add to `AutoTrpgAdminWeb`:

- `GET /dm/web/session/dumps?session_key=...` — list dump files with size and timestamp.
- `GET /dm/web/session/dump?session_key=...&turn_id=...` — fetch a single envelope.
- `GET /dm/web/session/export?session_key=...` — generate and return the forensic ZIP.

## Implementation Plan

### Phase 1: Foundation (No Router Changes)

**New files:**
- `core/forensic_collector.py` — `ForensicCollector` class:
  - `start_turn(session_id, cycle_id, actor, player_message)`
  - `record_state_before(dict)`, `record_state_after(dict)`
  - `record_prompts(system, user, specs, stats)`
  - `record_llm_request(step, prompt, contexts, system_prompt)`
  - `record_llm_response(step, raw_response_dict, completion_text, tool_calls, usage)`
  - `record_tool_execution(step, name, args, result, guard_blocked, guard_reason)`
  - `record_guard(name, decision, metadata)`
  - `record_fast_path(action, reply)`
  - `record_final_output(completion, dice_summary, pending_outputs)`
  - `build_envelope() -> dict`
- `core/forensic_diff.py` — `compute_session_diff(before, after) -> dict`

**Enhanced files:**
- `storage/json_repository.py`:
  - `turn_dumps_dir(session_id) -> Path`
  - `write_turn_dump(session_id, envelope) -> Path`
  - `list_turn_dumps(session_id) -> list[dict]`
  - `archive_session_forensic(session_id) -> Path`
  - `_rotate_turn_dumps(session_id)` — enforce max count / age

**Config integration (`main.py` init):**
- Read `forensic_dumps_enabled`, `forensic_max_turns_per_session`, `forensic_retain_days`, `forensic_include_prompts`, `forensic_include_raw_response` from plugin config.
- Store as instance variables on `AutoTrpgDmPlugin`.

### Phase 2: Instrument the Hot Path

**`main.py`:**
- In `_handle_dm_event()`, after deriving `session_id` and `actor`:
  - If `forensic_dumps_enabled`: create `ForensicCollector()` and pass it into `self.router.handle_message(event, ..., collector=collector)`.
  - If fast path returns early: use collector to record fast-path outcome and schedule dump write.
  - If security blocks: record security decision in collector and schedule dump write.

**`core/router.py`:**
- `IntentRouter.handle_message(...)`: add optional `collector: ForensicCollector | None = None`.
- `_handle_message_once(...)`: add optional `collector` param.
  - At entry: `collector.record_state_before(session.to_dict())`.
  - After prompt build: `collector.record_prompts(...)`.
  - After each guard fires: `collector.record_guard(...)`.
  - After RA/cycle resolution: `collector.record_post_processing(...)`.
  - At exit: `collector.record_state_after(session.to_dict())`, build envelope, schedule write.
- `_run_llm_tool_loop(...)`: add optional `collector` param.
  - Before `_llm_generate()`: `collector.record_llm_request(...)`.
  - After `_llm_generate()`: extract raw response dict via `_extract_raw_response_dict(response)`, then `collector.record_llm_response(...)`.
  - After each tool execution: `collector.record_tool_execution(...)`.
- `ToolLoopResult`: add optional `llm_trace: list[dict] | None = None` field (populated from collector before returning).

**No changes needed to:**
- `_llm_generate()`, `_llm_generate_once()`, `_llm_generate_raw()` — the caller (`_run_llm_tool_loop`) already has the response object in hand.

### Phase 3: Admin Web & In-Chat Export

**`core/admin_web.py`:**
- `get_session_turn_dumps(session_key)` — list dumps.
- `get_session_turn_dump(session_key, turn_id)` — fetch one envelope.
- `export_session_forensic(session_key)` — call `repository.archive_session_forensic()` and return zip path.

**`tools/memory_tools.py`:**
- In `session_control()`, add `export_forensic` action:
  - Check if dumps enabled.
  - Call `repository.archive_session_forensic()`.
  - Return zip path and turn count.

### Phase 4: Validation

**New tests:**
- `test_forensic_collector.py` — unit test collector assembly.
- `test_forensic_diff.py` — unit test diff engine.
- `test_json_repository_dumps.py` — test dump write/rotation/archive.
- `test_router_forensic.py` — verify collector receives events when passed.

## Interface Changes & Compatibility

**Zero breaking refactors.**

All changes are additive:

| Change | Impact |
|---|---|
| Optional `collector` param on `handle_message`, `_handle_message_once`, `_run_llm_tool_loop` | Zero impact on existing callers. |
| New optional field `llm_trace` on `ToolLoopResult` | Existing code only accesses `completion_text` and `tool_results`; untouched. |
| New repository methods (`write_turn_dump`, `archive_session_forensic`, etc.) | Pure addition; no existing signatures changed. |
| New config keys | Default `false` / standard defaults; plugin behaves identically if absent. |
| New module files | No import side effects on existing code unless explicitly imported. |

The session save format (`GameSession.to_dict()`) is **not modified**. Dumps live in a separate directory tree.

## Open Questions (Deferred to Post-MVP)

1. **Raw response field coverage** — Different providers (OpenAI, Claude, Gemini, local) return different response shapes. `_extract_raw_response_dict()` should start with a conservative extraction and expand as needed.
2. **Dump viewer / analyzer script** — A CLI or notebook script that reads a dump folder and produces a markdown timeline report. Not in MVP.
3. **Compression for archival** — Keep plain JSON for active analysis; optionally compress dumps older than `forensic_retain_days` in a background cleanup task.

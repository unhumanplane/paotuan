# Forensic Dump System

## What it is

The forensic dump system is an **opt-in sidecar observability layer** that captures the complete causal chain of every DM turn — state before/after, exact prompts, raw LLM responses, tool executions, guard decisions, and semantic diffs — into a single self-contained JSON file per turn.

When enabled, you can reconstruct exactly what the agent saw, why it made a decision, and what changed as a result. When disabled, there is zero overhead and zero behavioral change.

## Enable

Add to your AstrBot plugin config (`data/auto_trpg_dm/config.json` or via the web UI):

```json
{
  "forensic_dumps_enabled": true,
  "forensic_max_turns_per_session": 500,
  "forensic_retain_days": 30,
  "forensic_include_prompts": true,
  "forensic_include_raw_response": true
}
```

| Flag | Default | Meaning |
|---|---|---|
| `forensic_dumps_enabled` | `true` | Master switch. `false` = collector is never created. |
| `forensic_max_turns_per_session` | `500` | Max dump files kept per session. Older files are deleted automatically. |
| `forensic_retain_days` | `30` | Dump files older than this are deleted regardless of count. |
| `forensic_include_prompts` | `true` | If `false`, prompts are replaced with char counts and hashes (privacy/size). |
| `forensic_include_raw_response` | `true` | If `false`, raw LLM response objects are omitted. |

## What gets captured per turn

One plain JSON file is written for every player `/dm` turn (and system turns such as RA cycle resolution). The file is called a **Turn Envelope**.

### Envelope contents

| Section | Description |
|---|---|
| `turn_id` / `session_id` / `cycle_id` | Unique turn identity and routing context. |
| `timings` | Start/end ISO timestamps. |
| `actor` | Player ID and display name. |
| `player_message` | The exact redacted player input. |
| `routing` | Mode (`narrative` / `tactical` / `setup`), provider, security notes, fast-path info. |
| `state.before` | Full `GameSession.to_dict()` snapshot at turn start. |
| `state.after` | Full `GameSession.to_dict()` snapshot at turn end. |
| `state.diff` | Semantic diff highlighting characters created/removed/modified, scene changes, battle mutations, timeline additions, map changes, etc. |
| `prompts` | System prompt, user prompt, tool names/specs, projection stats, component char counts. |
| `llm_interactions[]` | One entry per LLM call within the turn (prompt, contexts, raw response, usage, finish reason, tool calls). |
| `tool_executions[]` | Per-interaction: tool name, arguments, result, guard block status. |
| `guards_fired[]` | Action reasonableness, action economy, outbound menu cleanup, semantic review, continuity audit, deterministic repair. |
| `ra_resolution` | RA cycle input/output when a resolution cycle runs. |
| `final_output` | Completion text, dice summary, pending outputs, and whether it was sent to the player. |

## Storage layout

```
<data_dir>/
  saves/<session_id>.json          # primary session save (unchanged)
  audit/<session_id>.jsonl         # audit log (unchanged)
  dumps/<session_id>/              # NEW: one folder per session
    20260519_154230_005_a1b2c3.json
    20260519_154245_006_d4e5f6.json
    ...
```

Files are plain JSON (no gzip by default) so you can `cat`, `grep`, or open them directly.

## Export a session

The repository exposes `archive_session_forensic(session_id)` which produces a ZIP containing:

- `session_save.json` — latest session snapshot
- `audit.jsonl` — full audit trail
- `plugin_log_tail.txt` — last N lines of plugin log
- `backups_manifest.json` — list of available backups
- `dumps/*.json` — all turn envelopes for the session
- `README.txt` — summary and file index

## Performance & retention

- **Hot path**: Dump write happens in a fire-and-forget `asyncio.create_task` after the player reply has already been yielded. It never blocks the response.
- **Rotation**: Automatic. When the per-session count exceeds `forensic_max_turns_per_session` or files age past `forensic_retain_days`, oldest files are deleted.
- **Size**: Envelopes vary with session size and prompt length. Typical size is tens to hundreds of KB per turn. With default limits, a session's dump folder is bounded to roughly a few hundred MB at most.
- **Privacy**: Set `forensic_include_prompts: false` to strip exact prompt text while keeping hashes and char counts.

## How it works (architecture)

```
_handle_dm_event()
  └─> creates ForensicCollector (if enabled)
      └─> router.handle_message(..., collector=collector)
          └─> _handle_message_once()
              ├─> collector.start_turn()
              ├─> collector.record_state_before()
              ├─> collector.record_prompts()
              ├─> collector.record_guard()  [at each guard stage]
              ├─> _run_llm_tool_loop()
              │   ├─> collector.record_llm_request()
              │   ├─> collector.record_llm_response()
              │   └─> collector.record_tool_execution()
              ├─> collector.record_ra_resolution()
              ├─> collector.record_state_after()
              ├─> collector.record_final_output()
              └─> collector.build_envelope()
                  └─> asyncio.create_task(repository.write_turn_dump(...))
```

All `collector` parameters are optional (`None` by default), so existing tests and call sites are unaffected.

## Related documents

- Design PRD: [forensic-dump-system-prd.md](forensic-dump-system-prd.md)

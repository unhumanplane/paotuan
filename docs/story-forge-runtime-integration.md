# Story Forge Runtime Integration

This note describes the production Story Forge runtime now wired into the auto TRPG DM plugin.

## Goals

- Archive every `/dm` turn so a campaign has a durable script record.
- Keep writer/planning state separate from player-facing narration.
- Let the DM submit structured next-scene goal cards when visible evidence supports convergence.
- Render player-safe `map_grid_seed` data through the deterministic strict-grid SVG renderer.
- Preserve hidden-truth and map-projection safety boundaries.

## Responsibility Layers

Story Forge is defined as three cooperating layers in production:

- Writer layer: converts player-visible material into a runnable next-scene goal. It may create `record_story_forge_convergence` cards, but it must not invent unrevealed truth or replace player choices.
- Narrative DM layer: speaks to the players, describes observable consequences, presents pressure, and keeps agency live at the table.
- Record / referee layer: uses rules, scene, timeline, turn, character, and spatial tools to settle authoritative facts before any writer-layer convergence card is recorded.

The runtime order is therefore: referee state first, player-facing narration second, writer convergence third, archive/map postprocess last. This mirrors the Script Forging idea that AI can forge structure, while the system keeps checkpoints, continuity locks, and self-audit boundaries visible.

## Runtime Flow

1. `/dm` runs through the normal router/tool loop.
2. After the router returns a player-facing completion, `AutoTrpgDmPlugin._apply_story_forge_runtime_after_reply()` runs before `_pop_pending_outputs()`.
3. `apply_story_forge_turn()` stores the raw turn in `scene["_story_forge_archive"]`.
4. A compressed, player-safe `scene["story_forge_player_brief"]` is generated for future prompt context.
5. If any convergence actions contain unrendered player-safe map seeds, the runtime renders them to SVG and enqueues normal `svg_map` pending output.
6. The existing pending-output delivery path attaches the SVG preview to chat if cadence allows it.

## Tool Contract

The tool `record_story_forge_convergence` records a next-scene goal card:

- `scene_goal`: concrete next scene objective.
- `entry_cost`: risk, resource, time, position, or fictional commitment needed to enter.
- `success_signal`: what the players can observe on success.
- `failure_forward`: how the game continues on failure.
- `evidence`: visible clues, tool results, or scene facts supporting the goal.
- `map_grid_seed`: optional player-visible grid seed for deterministic SVG rendering.

This upgrades convergence from passive logging to a runnable scene target: the next DM turn has an entry cost, observable success signal, failure-forward path, and evidence trail instead of a vague note.

The tool rejects hidden-truth fields such as `hidden_truth`, `secret_*`, `*_hidden`, and hidden/secret/private/DM-only records.

## Map Loop

The map loop is intentionally data-first:

1. The writer layer may attach a player-safe `map_grid_seed` when the next scene benefits from routes, doors, obstacles, NPC positions, or interactable points.
2. `record_story_forge_convergence()` stores that seed as part of the scene-goal card.
3. `render_story_forge_map_seed()` adapts the seed into strict-grid render input.
4. The deterministic strict-grid SVG renderer produces the player-view map.
5. The existing pending-output delivery path sends the SVG as a normal map attachment.

The SVG remains a view. Authoritative coordinates, movement, range, line of sight, and battle state still belong to the map / battle tools.

## Archive Infrastructure

Each `/dm` reply is archived as runtime infrastructure, not as a prompt dump. The raw archive records turn text hashes, bounded player/DM text, actor metadata, visible tracking state, clue ledger, open threads, convergence cards, rendered map references, and audit events. Ordinary DM prompts receive only `story_forge_player_brief`, which is compressed and player-safe.

## Data Model

Internal archive:

```json
{
  "_story_forge_archive": {
    "schema_version": 1,
    "turns": [],
    "open_threads": [],
    "thread_progress": [],
    "clue_ledger": [],
    "convergence_actions": [],
    "rendered_map_refs": [],
    "updated_at": ""
  }
}
```

Prompt-safe brief:

```json
{
  "story_forge_player_brief": {
    "schema_version": 1,
    "open_threads": [],
    "clue_ledger": [],
    "convergence_actions": [],
    "rendered_maps": []
  }
}
```

`_story_forge_archive` is dropped from ordinary DM prompt projection. `story_forge_player_brief` is projected through the normal visible-scene projection.

## Configuration

- `story_forge_runtime_enabled`: master switch.
- `story_forge_archive_enabled`: per-turn archive switch.
- `story_forge_map_seed_render_enabled`: deterministic map seed rendering switch.
- `story_forge_map_seed_send_to_chat`: enqueue rendered maps for chat delivery.
- `story_forge_archive_max_turns`: retained raw turn count.
- `story_forge_turn_text_max_chars`: max chars per archived player/DM text.
- `story_forge_max_open_threads`: player-safe open-thread budget.
- `story_forge_max_clue_ledger`: player-safe clue-ledger budget.
- `story_forge_max_convergence_actions`: retained/projected goal card budget.

## Safety Boundaries

- Do not expose raw archive turns in ordinary prompts.
- Do not write hidden truth, true culprit, hidden locations, or secret motives into player-safe scene fields or map seeds.
- `record_story_forge_convergence` does not replace `resolve_check`, `execute_rule`, `update_scene`, `turn_control`, or spatial tools.
- SVG output is visual-only and never becomes authoritative position, movement, line-of-sight, or distance state.
- Map seed rendering uses player-view strict-grid adapter behavior and existing map delivery cadence.

## Verification

Targeted checks:

```powershell
python -m compileall -q astrbot_plugin_auto_trpg_dm scripts tests
python -m pytest -q tests/test_story_forge_runtime.py tests/test_tool_registry.py tests/test_prompts.py tests/test_prompt_projection.py tests/test_strict_grid_render_tools.py tests/test_render_story_grid_map.py
python -m json.tool astrbot_plugin_auto_trpg_dm\_conf_schema.json > $null
```

Full local check:

```powershell
python -m pytest -q
```

DeepSeek runtime suites:

```powershell
. .\.story-forge-runs\story_forge_env.ps1
python scripts\story_forge_compare.py --simulation-suite-file .story-forge-runs\simulation_suite_lighthouse_secret_only.json --with-judge --audit-simulation --repair-from-audit --model $env:STORY_FORGE_DEEPSEEK_MODEL --base-url $env:STORY_FORGE_DEEPSEEK_BASE_URL --max-tokens 8192 --judge-max-tokens 4096 --temperature 0.7 --judge-temperature 0.2 --timeout 180 --output-dir .story-forge-runs
python scripts\story_forge_compare.py --simulation-suite-file .story-forge-runs\simulation_suite_multigenre.json --with-judge --audit-simulation --repair-from-audit --model $env:STORY_FORGE_DEEPSEEK_MODEL --base-url $env:STORY_FORGE_DEEPSEEK_BASE_URL --max-tokens 8192 --judge-max-tokens 4096 --temperature 0.7 --judge-temperature 0.2 --timeout 180 --output-dir .story-forge-runs
```

Latest observed DeepSeek v4 flash evidence:

- Lighthouse hidden-truth regression baseline: `.story-forge-runs/20260608T131015Z-suite-runtime-secret-safety-lighthouse-only/suite_report.md`, 1/1 pass, total 75, hidden-truth safety 10, playability 9, token total 171062.
- Lighthouse cache-optimized regression: `.story-forge-runs/20260608T143343Z-suite-runtime-secret-safety-lighthouse-only/suite_report.md`, 1/1 pass, total 76, hidden-truth safety 10, narrative momentum 9, playability 9, token total 113390, prompt cache hit ratio 42.08%. This is a 33.7% token reduction versus the baseline while preserving the pass verdict.
- Wider multigenre regression: `.story-forge-runs/20260608T132313Z-suite-runtime-acceptance-multigenre-v1/suite_report.md`, 5/5 pass, average total 77, hidden-truth safety 10 across all cases, narrative momentum 9 across all cases, playability 9 across all cases, reported token total 918099.
- Map seed render evidence for the wider suite: 9/9 `map_grid_seed` payloads rendered successfully into per-case `rendered_maps_manifest.json` plus SVG files after the CLI import path fix.

The DeepSeek regression tool keeps stable instructions, compact Story Forge runtime context, and output schemas before per-turn archive/action data so DeepSeek's automatic prefix cache can hit more often. It also records `prompt_cache_hit_tokens`, `prompt_cache_miss_tokens`, and hit ratio in suite reports.

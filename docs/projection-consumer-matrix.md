# Projection Consumer Matrix

This handoff document records which runtime consumers may read each map projection before renderer work starts. It sits between the state ownership / projection work and the renderer player-view delivery work.

The core rule is code-owned: projection choice, snapshot filtering, hidden fact removal, and compatibility-field routing must be handled by deterministic code. LLMs and agents may consume already-projected views and sanitized tool results, but they must not decide whether hidden or backend facts are safe to show.

## Scope

This document covers:

- ordinary DM narration;
- RA authority and maintenance flow;
- renderer and player map output;
- diagnostics;
- map tools;
- battle tools;
- tests and fixtures;
- legacy compatibility paths.

It does not implement a deterministic renderer, remove legacy fields, redesign character/control ownership, add hidden dice, or introduce per-character private vision.

## Dependency Position

This handoff sits after 03.1.06 state ownership / snapshot projection work and before 03.1.07 renderer work.

- 03.1.06 defines the state ownership and projection boundary.
- 03.1.06.01 verifies consumer intent and residual ambiguity without changing renderer behavior.
- 03.1.07.00 owns the legacy visual-map entrypoint inventory and minimal renderer request/output envelope.
- 03.1.07 renderer child stages may consume this matrix, but must not rediscover or weaken the projection boundary.
- 03.1.08 owns legacy cleanup and migration finalization after adapters, projections, and renderer paths stabilize.

The current handoff decision is intentionally narrow: document and verify projection consumers; do not migrate `generate_map_svg()`, delete `battle.grid`, or implement renderer output.

## Projection Views

| View | Intended consumers | Allowed visibility / payload |
| --- | --- | --- |
| `player_view` | Player-facing map output and future renderer input. | `public` and `player` facts, safe render refs, player-visible map metadata. |
| `dm_narration_view` | Ordinary DM prompt and gameplay narration. | `public`, `player`, and `dm` facts plus prompt-safe summaries. |
| `ra_authority_view` | Recorder Agent authority and maintenance analysis. | Sanitized authority snapshot with `public`, `player`, and `dm` map facts. |
| `diagnostic_view` | Explicit diagnostics and projection telemetry. | Counts, metadata, and diagnostic summaries without fact payloads. |

Raw `GameSession.maps`, raw strict grids, hidden facts, diagnostic-only payloads, local artifact paths, provider URLs, raw SVG, raw tool traces, raw RA output, raw web grounding records, and raw rule-package internals are not valid ordinary DM or player inputs.

## Scan Evidence

| Area | Evidence |
| --- | --- |
| Projection API | `astrbot_plugin_auto_trpg_dm/core/map_core.py` defines `MAP_VIEW_PLAYER`, `MAP_VIEW_DM_NARRATION`, `MAP_VIEW_RA_AUTHORITY`, `MAP_VIEW_DIAGNOSTIC`, and `project_map_store()`. |
| Ordinary DM prompt | `astrbot_plugin_auto_trpg_dm/core/prompts.py` builds prompt snapshot data through `_project_snapshot_for_profile()` and injects `project_map_store(session.maps, MAP_VIEW_DM_NARRATION)`. |
| Tool results returned to DM | `astrbot_plugin_auto_trpg_dm/core/router.py` projects second-pass tool context through `project_tool_results_for_dm_prompt()`. |
| Prompt guard | `astrbot_plugin_auto_trpg_dm/core/prompt_projection.py` blocks backend, hidden, diagnostic, path, URL, raw SVG, web grounding, and raw rule package payloads. |
| RA authority | `astrbot_plugin_auto_trpg_dm/core/environment_agent.py` builds RA input and authority snapshots through `sanitize_ra_payload()` and `MAP_VIEW_RA_AUTHORITY`. |
| Legacy visual output | `astrbot_plugin_auto_trpg_dm/tools/map_tools.py` still implements `generate_map_svg()` and writes `scene["last_map_svg"]` plus `_pending_outputs`. |
| Player delivery | `astrbot_plugin_auto_trpg_dm/main.py` pops `_pending_outputs` and delivers `svg_map` previews. |
| Battle tools | There is no `tools/battle_tools.py`; the battle surface is `tools/spatial_tools.py`, `tools/strict_lifecycle_tools.py`, and `tools/registry.py`. `get_battle_snapshot()` now returns a prompt-safe `battle_status` / `tactical_map` summary instead of raw `session.battle` or raw strict grid. |
| Diagnostics | `tools/diagnostic_tools.py` and `tools/memory_tools.py` can inspect broader snapshots, save paths, audit paths, plugin log paths, and recent audit records. |
| Tests | `tests/test_map_core.py`, `tests/test_environment_agent.py`, `tests/test_prompt_projection.py`, `tests/test_prompts.py`, `tests/test_router_usage.py`, and `tests/test_spatial_tools.py` cover projection filtering, RA sanitization, prompt projection, raw strict-grid exclusion, and legacy battle-grid migration. |

Additional scan notes:

- No production deterministic `player_view` renderer consumer exists yet. `MAP_VIEW_PLAYER` is currently exercised by projection code and tests, not by a runtime renderer.
- `tools/map_tools.py` still reads `session.compact_snapshot().get("battle", {})` inside `generate_map_svg()` and derives grid dimensions from legacy battle state. This is a visual-only compatibility path, not the renderer contract.
- `main.py` still treats `_pending_outputs` as delivery infrastructure and may read internal artifact paths for chat attachment. Those paths are delivery-only metadata and must not become prompt/player facts.
- `get_battle_snapshot()` no longer returns raw `session.battle` or `grid.to_dict()` to ordinary tool callers. It keeps old-save strict-grid migration side effects, then returns safe battle/map summaries while raw grids remain internal to MapStore/spatial tools.
- Projection fixtures are inline tests rather than a tracked fixture directory. Later runtime changes should add focused regression tests in the same PR that changes behavior.

## Consumer Matrix

| Consumer | Intended projection | Allowed fields | Explicitly forbidden raw fields or hidden facts | Compatibility fields still tolerated | Migration target | Test or scan evidence | Blocker / follow-up |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Ordinary DM narration | `dm_narration_view` plus prompt-safe tool and RA summaries. | Public/player/DM map facts, safe render refs, battle/scene/rule summaries, projected tool results, projected RA summaries. | Raw `session.maps`, hidden or diagnostic map facts, raw strict grid, legacy `battle.grid`, raw RA output, raw tool traces, future web grounding records, hidden rule-package internals, local paths, provider URLs, raw SVG, diagnostic-only state. | `snapshot_projection_enabled=False` remains a debug/config escape hatch. Some environment summary data may still have multiple entry paths and should be treated as prompt-safe only after projection. | Keep ordinary gameplay prompts on code-owned projection. Add targeted guards if debug escape hatches can be reached from normal gameplay routing. | `core/prompts.py`, `core/router.py`, `core/prompt_projection.py`, `tests/test_prompts.py`, `tests/test_prompt_projection.py`, `tests/test_router_usage.py`. | Follow-up: make any full-snapshot mode explicitly diagnostic/debug-only, not ordinary gameplay narration. |
| RA authority / maintenance flow | `ra_authority_view` plus sanitized `ra_cycle_input`. | Sanitized authority snapshot, public/player/DM map facts, authority-facing battle/character/rule summaries, accepted/rejected patch counts. | Hidden facts, raw prompt text, raw player input, raw audit records, token/debug payloads, raw strict grid, local paths, provider URLs, raw rule packages. | RA is allowed to see broader authority context than player output, but only through `sanitize_ra_payload()` and explicit authority builders. | Keep RA authority separate from player-facing and ordinary DM narration projections. | `core/environment_agent.py`, `core/cycle_buffer.py`, `tests/test_environment_agent.py`. | Document RA output as authority material; ordinary DM may only consume projected RA summary. |
| Renderer / player map output | Target `player_view` plus explicit render request/envelope. | Player-visible map facts, safe render refs, map id/revision labels, render purpose, delivery metadata. | Raw MapStore authority state, DM-only facts, hidden facts, raw strict grid, legacy `battle.grid`, raw SVG as backend input, local file paths or URLs as prompt/player facts. | Current `generate_map_svg()` is a legacy visual-only path that reads compact battle state and writes `last_map_svg` / `_pending_outputs`. Delivery may still carry artifact paths internally. | 03.1.07.00 should define renderer input as `player_view + render request/envelope`; renderer output should be render refs / artifact metadata / delivery metadata only. | `tools/map_tools.py`, `main.py`, `tools/registry.py`, `tests/test_map_core.py`, `tests/test_router_usage.py`. | Blocker for renderer implementation: no production deterministic `player_view` renderer consumer exists yet. |
| Diagnostics | `diagnostic_view` for map projection; explicit diagnostic tools may inspect broader state. | Counts, record metadata, projection telemetry, compact/full snapshot sizes, audit byte counts, explicit diagnostic summaries. | Fact payloads and hidden text in `diagnostic_view`; diagnostic outputs must not feed ordinary gameplay narration without prompt projection. | `estimate_token_usage()` and `session_control(status/debug_last)` can return broader state, paths, and recent audit records. | Keep diagnostic profile/tooling isolated from ordinary DM prompts and player-facing delivery. | `core/prompts.py`, `tools/diagnostic_tools.py`, `tools/memory_tools.py`, `tests/test_prompts.py`, `tests/test_map_core.py`. | Follow-up: ensure diagnostic tool outputs are never reused as ordinary narration context without `project_tool_results_for_dm_prompt()`. |
| Map tools | Code-owned tool authority for map writes; future renderer-bound output should use `player_view`. | Strict MapStore writes, candidate validation results, visual-only render records, delivery metadata. | Hidden facts to renderer/player, raw local paths in ordinary DM prompt, raw SVG as backend fact source, direct LLM patches to `maps`. | `generate_map_svg()` remains an LLM-written SVG compatibility path; `last_map_svg.path` and `_pending_outputs.path` remain internal delivery fields. | Deterministic renderer behind an explicit request/envelope; render refs and delivery cache replace legacy prompt-visible visual output. | `tools/map_tools.py`, `tools/registry.py`, `docs/mapcore-projection-guard.md`. | 03.1.07.00 must inventory and classify legacy visual entrypoints before renderer PRDs rely on them. |
| Battle tools | Internal deterministic grid access; exposed gameplay summaries should be projected or sanitized. | Strict grid for local calculation, turn order, action economy, combat lifecycle, map link state, safe tactical summaries. | Raw `battle.grid`, raw `grid.to_dict()`, hidden entities, backend geometry, stale legacy mirror as authority, raw battle dump to ordinary DM/player context. | `get_battle_snapshot()` is exposed in tactical toolsets but now returns safe `battle_status` and `tactical_map` summaries. `battle.grid` mirror remains for old callers and saved sessions. There is no separate `tools/battle_tools.py`; battle-facing tools are split across spatial, strict lifecycle, turn, and registry code. | Keep ordinary battle queries on the safe summary; raw battle/grid inspection belongs to diagnostic/internal paths only. | `tools/spatial_tools.py`, `tools/strict_lifecycle_tools.py`, `tools/turn_tools.py`, `tools/registry.py`, `tests/test_spatial_tools.py`, `tests/test_strict_lifecycle_tools.py`, `tests/test_map_core.py`, `tests/test_cycle_buffer.py`. | Remaining risk is the compatibility mirror itself, not ordinary `get_battle_snapshot()` output. 03.1.08.01 should audit whether any mirror writer still has a real old-caller need. |
| Tests and fixtures | All four projection contracts. | Inline fixtures for hidden facts, raw paths, URLs, raw SVG, web/rule payloads, raw strict grid, and legacy battle-grid migration. | Tests should not normalize hidden/backend leakage as expected output. | No tracked fixture directory was found for projection handoff; current coverage is mostly inline tests. | Add focused regression tests when behavior changes. Docs-only handoff does not require new runtime tests. | `tests/test_map_core.py`, `tests/test_environment_agent.py`, `tests/test_prompt_projection.py`, `tests/test_prompts.py`, `tests/test_router_usage.py`, `tests/test_spatial_tools.py`. | If later PRs change tool exposure or renderer input, add behavior tests in the same PR. |
| Legacy compatibility paths | Compatibility-only; not a future authority contract. | `battle.grid` migration source and mirror, `last_map_svg`, `_pending_outputs`, internal artifact paths, debug/status paths. | Treating legacy fields as renderer input contract, source of map truth, ordinary prompt context, or player-facing fact payload. | Legacy `battle.grid`, LLM SVG path, local SVG file path fallback, projection-disable mode. | MapStore authority, explicit projections, render refs, delivery cache, safe battle/status projections. | `docs/mapcore-projection-guard.md`, `tools/spatial_tools.py`, `tools/map_tools.py`, `main.py`, `tests/test_map_core.py`, `tests/test_spatial_tools.py`. | Do not delete these in this handoff. Label them clearly so 03.1.07 and 03.1.08 can migrate them deliberately. |

## Handoff For Renderer Work

Renderer work may start only from the boundary that renderer input is `player_view` plus an explicit render request/envelope. Renderer output is a visual artifact reference and delivery metadata. The renderer must not:

- read raw MapStore facts directly;
- read hidden, DM-only, diagnostic, or raw strict-grid payloads;
- use SVG/PNG artifacts as backend input;
- write coordinates, topology, visibility, movement, range, line of sight, or adjudication facts;
- let an LLM write the normal final SVG/XML artifact.

Current `generate_map_svg()` is a legacy visual-only compatibility path. It should be inventoried and classified before deterministic renderer work relies on delivery behavior.

## Follow-Up List

1. Define the 03.1.07.00 renderer input/output boundary using this matrix: `player_view + render request/envelope` in, render refs / delivery metadata out.
2. Keep `get_battle_snapshot()` on its safe summary shape before broad player-facing map delivery; do not reintroduce raw battle/grid payloads into ordinary tool output.
3. Keep diagnostic tools isolated from ordinary gameplay narration. Diagnostic outputs can inspect broader state, but any reuse in DM context must go through prompt projection.
4. Treat `snapshot_projection_enabled=False` as debug/config behavior, not normal ordinary gameplay narration.
5. Keep `battle.grid`, `last_map_svg`, `_pending_outputs`, and local artifact paths as compatibility-only until focused migration PRs remove or replace them.
6. Add behavior tests only when changing runtime routing, renderer input, or tool exposure. This docs handoff records the contract and scan evidence without changing behavior.

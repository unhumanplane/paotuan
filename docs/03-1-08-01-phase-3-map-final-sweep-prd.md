# 03.1.08.01 Phase 3 Map Migration Final Sweep PRD

## Current / Already Done

Stage 03.1 has moved the map stack from legacy battle-coupled and visual-artifact-coupled state to a MapStore-owned architecture.

- 03.1.01 introduced `GameSession.maps`, MapStore-style records, role projections, render refs, candidate-event guardrails, and hidden-fact projection filtering.
- 03.1.02 made `battle.grid` a legacy migration source and compatibility mirror instead of the strict map authority.
- 03.1.03 moved `create_grid` writes through MapStore while preserving the old tactical tool behavior.
- 03.1.04 routed strict spatial operations through `MapCalculator` instead of binding movement and attack-vector logic directly to `battle.grid`.
- 03.1.05 separated strict local map lifecycle from combat lifecycle through strict map records and the `battle.map_id` combat link.
- 03.1.06 and 03.1.06.01 locked role projection ownership for DM, RA, renderer, diagnostics, tools, tests, and compatibility fields.
- 03.1.07 introduced deterministic strict-grid and overview-topology renderers that consume player-safe projections and write visual-only render refs.
- 03.1.07.04 added deterministic-first map tool routing, delivery cadence, legacy SVG fallback policy, old pending-output compatibility, and preview path privacy.
- 03.1.08 completed the first runtime cleanup pass: safe battle snapshots, RA raw-grid blocking, and MapStore-first turn/router/spatial guard readers.

## Prerequisite Status

03.1.08 is complete on the stacked predecessor branch and has been pushed to `upstream/feat/legacy-cleanup-migration-finalization`.

This final sweep is stacked on `feat/legacy-cleanup-migration-finalization` because 03.1.08 is not yet merged into `main`. The intended PR target for this task is therefore `feat/legacy-cleanup-migration-finalization`. If the predecessor branch is merged before this task opens a PR, the PR target may be retargeted to `main` after a fresh `upstream/main` check.

Blocking prerequisite rules:

- Do not start from `main` until 03.1.08 is merged there.
- Do not use the historical personal fork as a baseline or PR target.
- Do not remove compatibility paths before current readers, current writers, old-save behavior, tests, and rollback behavior are recorded.
- Treat this task as Phase 3 closure, not as another renderer feature or gameplay feature.

## Phase 3 Completion Definition

Phase 3 can be declared complete only when all of the following are true:

- MapStore / MapCore is the authoritative map state wherever prior stages promised it would be.
- No ordinary runtime component treats SVG, PNG, `scene["last_map_svg"]`, `_pending_outputs`, or local artifact paths as map truth.
- No ordinary runtime component parses player-facing visual artifacts as backend state.
- No ordinary runtime component reads stale `battle.grid` as authoritative when an active MapStore strict grid exists.
- Legacy fields are removed, inert, compatibility-only, or explicitly listed as non-blocking residual debt with tests and a compatibility window.
- New fields are not merely written; real target components read them.
- Tools, snapshots, prompts, RA input, renderer input, calculator routing, delivery, diagnostics, tests, and docs all consume the new fields or the intended role projections.
- Old-save migration paths load old sessions safely and cannot overwrite MapStore authority.
- DM, RA, renderer, and diagnostic views use their intended role projections.
- Strict map state, overview topology state, calculator routing, lifecycle state, archive/restore metadata, and renderer delivery have coherent ownership boundaries.

## Full Impact Scan Evidence

The final sweep covers these surfaces:

- map-related state and `GameSession.maps`;
- MapStore / MapCore authority helpers;
- strict-grid helper and adapter functions;
- spatial, strict lifecycle, turn, memory, map, renderer, and registry tools;
- compact snapshots and prompt snapshots;
- DM prompt projection and RA authority input;
- renderer input envelopes;
- strict-grid and overview-topology renderers;
- map delivery cadence and `_pending_outputs`;
- audit records and diagnostic paths;
- old-save migration loaders;
- docs, tests, and inline compatibility fixtures;
- `battle.grid`, `battle.map_id`, `scene["last_map_svg"]`, direct SVG assumptions, prompt snapshot keys, tool schemas, and old save/session records.

Current evidence:

- `core/map_core.py` owns `default_map_store()`, `normalize_map_store()`, `save_active_strict_grid()`, `load_active_strict_grid()`, `load_active_strict_grid_entities()`, and `migrate_legacy_battle_grid()`.
- `tools/spatial_tools.py` writes strict grids through MapStore and keeps `battle.grid` only as a mirror after the authoritative write.
- `tools/turn_tools.py` and `core/router.py` now use `load_active_strict_grid_entities()` for ordinary turn order, owner, and auto-advance reads.
- `tools/strict_grid_render_tools.py` loads MapStore-first strict grid state, builds a player-view envelope, renders deterministic SVG, writes visual-only render refs, and enqueues normalized pending output.
- `tools/overview_topology_render_tools.py` reads player-projected overview topology facts, uses deterministic layout, writes visual-only render refs, and enqueues normalized pending output.
- `core/prompts.py`, `core/environment_agent.py`, and `core/prompt_projection.py` project role-specific map views and block raw paths, raw SVG, raw grids, cadence keys, and layout internals from ordinary prompt surfaces.
- `core/map_delivery_cadence.py` normalizes old pending `svg_map` records without `render_type` as `legacy_generate_map_svg` and `visual_only`.
- `tools/map_tools.py` still implements `generate_map_svg()` and still writes `scene["last_map_svg"]`; this is an active legacy writer and a Phase 3 closure target.
- Some fast-path, compact snapshot, and memory summary paths still read `battle.grid.entities` directly. These are Phase 3 blockers because they are ordinary runtime readers, not old-save migration only.

## Migration Acceptance Matrix

| Stage | Promised field/path/tool change | Old readers before migration | Old writers before migration | Expected replacement field/path | Actual current readers | Actual current writers | Cleanup status for old fields | Evidence new fields are used by real components | Tests covering transition | Docs covering transition | Remaining gap or intentional debt | Blocks Phase 3 completion |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 03.1.01 MapCore skeleton | Add `GameSession.maps`, role projections, render refs, candidate guardrails | DM/RA snapshots, tools, diagnostics, and future renderers could rely on raw session state | Legacy tools wrote `battle`, `scene`, visual refs directly | `GameSession.maps` plus `project_map_store()` views | DM prompt, RA authority, renderer tools, diagnostics, tests | MapCore helpers and renderer tools | Raw map store is not valid ordinary prompt input | DM, RA, strict renderer, overview renderer, and tests read projections | `tests/test_map_core.py`, `tests/test_prompts.py`, `tests/test_environment_agent.py` | `docs/mapcore-projection-guard.md`, `docs/projection-consumer-matrix.md` | Full snapshot debug escape remains diagnostic-only debt | No, if ordinary paths stay projected |
| 03.1.02 legacy battle-grid adapter | Downgrade `battle.grid` to migration source / compatibility mirror | Spatial tools, battle snapshot, turn/router helpers, renderer fallback, compact battle | `create_grid`, movement, lifecycle, migration, renderer fallback | `session.maps.records[active_strict_map_id].grid` | MapCore helpers, spatial tools, renderer tools, turn/router helpers | MapStore strict grid writers plus compatibility mirror writers | Mostly downgraded, but direct ordinary readers remain | Turn/router/spatial guards use `load_active_strict_grid_entities()` | `tests/test_map_core.py`, `tests/test_spatial_tools.py`, `tests/test_turn_tools.py`, `tests/test_router_usage.py` | `docs/mapcore-projection-guard.md`, 03.1.08 PRD | `main.py`, `memory_tools.py`, and compact battle still need migration | Yes |
| 03.1.03 `create_grid` migration | Make `create_grid` write MapStore first | Existing callers expected `battle.grid` | `create_grid` wrote `battle.grid` as state | `save_active_strict_grid()` plus `battle.map_id` | Spatial tools, strict renderer, turn/router helpers | `create_grid` writes MapStore then mirrors `battle.grid` | Mirror kept for compatibility | Strict renderer and turn/router tests read MapStore result | `tests/test_spatial_tools.py`, `tests/test_map_core.py` | `docs/mapcore-projection-guard.md` | Confirm mirror is output-only and not read as authority by remaining paths | Partly |
| 03.1.04 MapCalculator routing | Route movement and attack-vector calculations through `MapCalculator` | Public tool handlers directly implied strict grid / `SpatialEngine` | Spatial tools saved calculations and audit results | `MapCalculator` route over loaded strict grid | `move_entity()` and `check_attack_vector()` | Spatial tool layer still owns save/audit/mirror side effects | Old direct route removed for covered ops | Real movement and attack-vector tools use calculator | `tests/test_map_calculator.py`, `tests/test_spatial_tools.py` | `docs/mapcore-projection-guard.md` | Add active map / route mismatch regression | No, if regression added |
| 03.1.05 strict lifecycle | Separate strict map lifecycle from combat lifecycle | Consumers treated `battle.active`, tactical mode, and grid presence as coupled | `create_grid`, combat start/end, spatial writes | strict lifecycle metadata plus `battle.map_id` combat link | strict lifecycle tools, prompts, mode gates, spatial tools | strict lifecycle tools and compatibility `create_grid` | `battle.active` remains combat-only; `battle.grid` remains mirror | `create_strict_map`, `start_combat_on_map`, `end_combat` exercise map records | `tests/test_strict_lifecycle_tools.py`, `tests/test_modes.py` | `docs/mapcore-projection-guard.md`, strict lifecycle spec | Need verify no final sweep path equates active strict map with combat | No |
| 03.1.06 role projections | Use owner-specific views for DM, RA, renderer, diagnostics | raw snapshots and compact snapshots | snapshot builders | `dm_narration_view`, `ra_authority_view`, `player_view`, `diagnostic_view` | DM prompt, RA authority, renderer tools, diagnostics | projection helpers only produce filtered views | Raw projected leakage mostly blocked | Prompt builders and RA builders use projections in runtime | `tests/test_prompts.py`, `tests/test_prompt_projection.py`, `tests/test_environment_agent.py` | `docs/projection-consumer-matrix.md`, `docs/mapcore-projection-guard.md` | `GameSession._compact_battle()` still derives entity context from `battle.grid` | Yes |
| 03.1.06.01 projection consumer matrix | Record consumer-specific projection contracts | Consumers could rely on compatibility fields | Docs-only handoff | renderer/player output consumes `player_view + envelope` | strict and overview renderers now consume player-view envelopes | renderer tools write render refs / pending output | Matrix needs final closure update | Renderer tools are real runtime consumers, not only tests | renderer and prompt projection tests | `docs/projection-consumer-matrix.md` | Update matrix from "future renderer" to "full rollout" | No, docs update required |
| 03.1.07.00 renderer input boundary | Inventory legacy visual entrypoints and define renderer IO | `generate_map_svg`, `last_map_svg`, `_pending_outputs` | legacy map tool wrote SVG, last ref, pending output | `player_view + render envelope` in; render refs and delivery metadata out | strict / overview renderer tools | render tools write render refs and pending output | Legacy path still implemented | Real renderer tools consume player projections | renderer tool tests, prompt projection tests | `docs/coordinate-renderer-contract.md` | Need stop new ordinary code from treating `last_map_svg` as active state | Yes |
| 03.1.07.01 coordinate contract | SVG/PNG cannot be map truth | visual map outputs and vague prose | LLM SVG fallback | structured coordinates, boundaries, anchors, paths, topology, revisions | renderer adapters and deterministic renderers | MapStore facts and renderer metadata | visual artifacts classified as visual-only | Strict and overview renderers read structured data | renderer tests | `docs/coordinate-renderer-contract.md` | Final docs must say legacy visual artifacts are deprecated | No |
| 03.1.07.02 strict renderer | Deterministic strict-grid SVG over player-safe input | `generate_map_svg` and raw battle grid fallback | legacy SVG path | `render_strict_grid_svg` over `player_view` | renderer tool and delivery path | render refs plus normalized pending output | legacy fallback still available explicit-only | Real tool writes artifact and delivery metadata | `tests/test_strict_grid_render_tools.py`, `tests/test_strict_grid_svg_renderer.py`, `tests/test_strict_grid_render_envelope.py` | `docs/strict-grid-svg-renderer-prd.md` | Add stale `battle.map_id` / active map mismatch regression | No, test gap |
| 03.1.07.03 overview renderer | Deterministic overview topology SVG over player-visible topology | `generate_map_svg` or narrative topology assumptions | legacy SVG path | `render_overview_topology_svg` over player-view topology facts | renderer tool and delivery path | render refs, layout cache, pending output | legacy fallback still available explicit-only | Real overview renderer reads player-visible topology facts | `tests/test_overview_topology_renderer.py`, `tests/test_overview_topology_render_tools.py` | `docs/overview-topology-svg-renderer-prd.md` | Add strict/overview active mismatch negative test | No, test gap |
| 03.1.07.04 delivery cadence | Deterministic-first routing; `generate_map_svg` explicit-only | normal map requests exposed LLM SVG path | legacy map tool, pending output | `map_tool_routing.py`, deterministic renderer tools, cadence state | registry, prompts, render tools, main pending delivery | renderer tools and legacy map tool | cadence compatible; legacy writer remains active | ordinary map requests expose deterministic renderer tools | `tests/test_map_delivery_cadence.py`, `tests/test_tool_registry.py`, `tests/test_prompts.py` | `docs/delivery-cadence-legacy-svg-migration-prd.md` | decide whether legacy fallback stays explicit-only or diagnostic-only | Yes |
| 03.1.08 cleanup | Narrow raw battle outputs and ordinary strict-grid readers | raw battle snapshots, raw grid RA payload, stale mirror readers | snapshot tool, tool-result buffers | safe `battle_status` / `tactical_map`; MapStore entity reader | spatial snapshot, RA sanitizer, turn/router helpers | safe snapshot writer; mirror writers remain | ordinary turn/router/spatial guard reads migrated | Real turn/router helpers read MapStore entities | `tests/test_spatial_tools.py`, `tests/test_cycle_buffer.py`, `tests/test_turn_tools.py`, `tests/test_router_usage.py` | 03.1.08 PRD, `docs/mapcore-projection-guard.md` | remaining ordinary readers and visual writer cleanup | Yes |

## Legacy Deprecation / Removal Matrix

| Legacy path | Current readers | Current writers | Replacement | Compatibility window | Old-save behavior | Rollback path | Tests | Final-sweep action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `battle.grid` as authority | Some fast-path, compact snapshot, and memory summary readers still read entities directly | mirror writers in spatial, strict renderer migration, strict lifecycle | `load_active_strict_grid()` / `load_active_strict_grid_entities()` and projected summaries | Only old-save migration and explicit compatibility mirror after this task | If MapStore has no strict grid, migrate legacy grid into strict record; never overwrite existing MapStore strict grid | Restore direct reads only as compatibility fallback guarded by MapStore-first precedence | map core, spatial, turn, router, prompts, memory tests | Migrate remaining ordinary readers; classify remaining mirror as compatibility-only |
| `battle.grid` mirror writers | old callers and tests may still observe mirror | `spatial_tools.py`, `strict_grid_render_tools.py`, `strict_lifecycle_tools.py` | authoritative MapStore write first; mirror as output copy | Keep only if still needed by old saves or external tool compatibility | Old sessions can still load mirror; new authority remains MapStore | Re-enable mirror writes per writer if external compatibility breaks | spatial, lifecycle, renderer tests | Audit each writer and mark compatibility-only or inert |
| `battle.map_id` | combat lifecycle and strict map link consumers | strict lifecycle and compatibility combat paths | Keep as combat-to-strict-map link | Permanent until a different combat link field replaces it | Old battle state may link to migrated strict map | Restore previous link behavior | strict lifecycle tests | Keep, not deprecated |
| `scene["last_map_svg"]` | safe old-save projection; legacy prompt snapshot compatibility | `generate_map_svg()` | MapStore `render_refs` and normalized pending output metadata | Read old records only; no active-state meaning | Project only safe fields such as type, title, name, visual-only flag | Restore legacy write if explicit fallback compatibility breaks | prompts and projection tests | Stop treating as active render state; remove or narrow new writes |
| `generate_map_svg` | explicit fallback route, registry, prompt guidance | writes SVG, `last_map_svg`, legacy pending output, audit | `render_strict_grid_svg` and `render_overview_topology_svg` | Explicit legacy/fallback/migration only, or diagnostic-only if implementation permits | Old sessions do not require new calls; old pending output handles old artifacts | Restore explicit fallback exposure without restoring ordinary exposure | registry, prompts, projection, delivery tests | Keep only bounded fallback or diagnostic path; remove normal map route assumptions |
| direct SVG / PNG as backend fact source | should have no ordinary readers | legacy visual tool writes artifact files | structured map facts, renderer layout metadata, render refs | No authority compatibility window | Old artifacts remain visual-only | Restore visual delivery, never authority behavior | renderer no-writeback tests | Verify and document visual-artifact non-authority |
| `_pending_outputs` | chat delivery pop/filter path | renderer tools, legacy map tool, other delivery producers | delivery queue metadata only | Keep while chat delivery uses this queue | Old records normalize or drop safely | Restore prior pop behavior if cadence breaks delivery | delivery and DM output tests | Keep internal; continue projection drop |
| pending output `path` | internal attachment and preview conversion | renderer tools and legacy map tool | internal delivery metadata | Internal only | old paths may remain internally | restore path stripping rules | DM output and projection tests | Keep internal, never prompt/player fact |
| old pending `svg_map` without `render_type` | cadence compatibility filter | old sessions only | `legacy_generate_map_svg`, `visual_only` normalized metadata | Keep as old-save reader | normalize on delivery | restore legacy delivery default | delivery cadence tests | Keep compatibility-only |
| prompt snapshot `last_map_svg` | scene projection safe ref | old scene record | safe render ref projection | Read old records only | project without raw path / SVG | restore safe projection | prompts tests | Keep as historical metadata only |
| prompt/tool result raw path, SVG, layout, cadence keys | prompt projection filters | tool results and delivery metadata | prompt-safe tool result projection | No ordinary prompt compatibility | old results are projected conservatively | restore blocked key list | prompt projection tests | Keep blocked; expand final whitelist tests |
| RA raw `battle` / `grid` tool payload | cycle buffer sanitizer | tool results captured into action buffers | safe summaries and RA authority projection | No raw RA input compatibility | old buffers should sanitize or ignore raw grid | restore sanitizer allowlist only with explicit rollback | cycle buffer and RA tests | Add cross-round stale grid sanitizer coverage |
| audit records | diagnostics only | tools and router/main audit | diagnostic-only audit | Keep internal | old audit remains readable but non-authoritative | restore audit write shape | diagnostic/projection tests | Keep internal; never ordinary prompt input |

## New Map Module Full Rollout Plan

The new map module rollout is a reader-first and writer-second migration:

1. Migrate every remaining ordinary runtime reader from `battle.grid` to MapStore-first helpers or safe projections.
2. Add regression tests for stale mirrors, multiple strict maps, inconsistent active map IDs, compact snapshots, and memory summary paths.
3. Keep `battle.grid` writes only as output mirrors after authoritative MapStore writes.
4. Move active visual metadata from `scene["last_map_svg"]` to render refs and normalized pending output metadata.
5. Keep `_pending_outputs` as delivery infrastructure only.
6. Update docs so renderer and delivery consumers are described as current rollout state, not future work.

## Old Map Module Deprecation Plan

The old map module is deprecated as an authority source.

Allowed old-path roles after this task:

- `battle.grid`: old-save migration source and compatibility mirror only.
- `battle.map_id`: combat link, not deprecated.
- `scene["last_map_svg"]`: historical visual metadata only.
- `generate_map_svg`: explicit fallback/migration or diagnostic visual tool only.
- legacy pending `svg_map`: delivery compatibility only.
- local artifact path: internal delivery implementation only.

Forbidden old-path roles after this task:

- map truth;
- renderer input contract;
- prompt fact source;
- RA authority source;
- turn ownership authority;
- combat lifecycle authority;
- movement, line-of-sight, range, topology, or visibility authority;
- source material for writing back MapStore facts.

## MapStore Authority Verification

Required verification:

- stale `battle.grid` cannot change movement, line of sight, attack vector, range, turn order, owner checks, auto-advance labels, memory summary character selection, prompt relevant-character selection, or renderer input when MapStore strict grid exists;
- `create_grid`, `create_strict_map`, `start_combat_on_map`, `end_combat`, `move_entity`, `place_entity`, `check_attack_vector`, strict renderer, overview renderer, and battle snapshot paths read or write the intended MapStore/projection path;
- old-save migration only runs when MapStore lacks a strict grid;
- mirror writes happen after authoritative writes, not before source selection.

## Visual Artifact Non-Authority Verification

Required verification:

- strict-grid SVG output is not written to `facts`, `grid`, topology facts, or layout authority;
- overview topology SVG output is not written to topology facts or hidden layout data;
- generated layout cache stays under renderer archive metadata rather than map facts;
- `scene["last_map_svg"]`, raw SVG text, PNG preview output, local paths, and provider URLs do not enter ordinary DM, RA, renderer, or player map-fact inputs;
- delivery failure messages never expose internal local paths.

## Role Projection Verification

Role projection contract:

- DM narration reads `dm_narration_view` plus prompt-safe tool results.
- RA reads `ra_authority_view` plus sanitized RA cycle input.
- Renderer reads `player_view` plus explicit render envelopes.
- Diagnostics read `diagnostic_view` or explicit diagnostic tool output.

Final sweep tests should lock the final projected shape for `maps`, `battle_status`, `tactical_map`, render refs, old visual metadata, and blocked delivery keys.

## Old Save / Compatibility Verification

Required old-save behavior:

- old sessions without `maps` load with a default MapStore;
- old sessions with only `battle.grid` migrate when strict map operations need a grid;
- old sessions with both MapStore strict grid and stale `battle.grid` keep MapStore authority;
- old sessions with `scene["last_map_svg"]` project only safe historical metadata;
- old sessions with pending `svg_map` and no `render_type` normalize to legacy visual-only delivery;
- old sessions with inconsistent `battle.map_id` and `active_strict_map_id` fail or resolve through a deterministic code path, never through prompt inference.

## Tests And Regression Plan

Add or update focused tests for:

- remaining direct `battle.grid.entities` readers in `main.py`, `memory_tools.py`, prompt relevant-character selection, and compact battle output;
- stale ghost entity in `battle.grid` not leaking through `get_battle_snapshot`, renderer input, memory summaries, or prompt snapshots;
- old save with `battle.map_id` present but `active_strict_map_id` missing or inconsistent;
- multiple strict maps and active map switch behavior;
- strict renderer ignoring stale `battle.map_id` when MapStore active strict map is authoritative;
- overview renderer refusing to render a strict active map as overview topology;
- strict and overview pending outputs not deduping each other incorrectly;
- tool schema contract for renderer and map lifecycle tools;
- RA cross-round sanitizer blocking stale legacy raw grid;
- final prompt snapshot field whitelist.

## Docs Finalization Plan

Update public docs in the final docs commit:

- `docs/mapcore-projection-guard.md`: mark MapStore full rollout and final legacy mirror policy.
- `docs/projection-consumer-matrix.md`: change renderer consumers from future/transition language to current rollout language.
- `docs/coordinate-renderer-contract.md`: mark SVG/PNG non-authority as final Phase 3 policy.
- `docs/strict-grid-svg-renderer-prd.md`: mark strict renderer as normal deterministic path.
- `docs/overview-topology-svg-renderer-prd.md`: mark overview renderer as normal deterministic path.
- `docs/delivery-cadence-legacy-svg-migration-prd.md`: record final `generate_map_svg` compatibility window and delivery metadata policy.
- `docs/03-1-08-legacy-cleanup-migration-finalization-prd.md`: cross-reference final sweep outcome.

## Remaining Debt / Follow-Up Issues

Debt may remain only if it is:

- not an ordinary runtime authority path;
- not a prompt/player/RA leakage path;
- not a writer that can overwrite MapStore authority;
- covered by compatibility or migration tests;
- documented with a compatibility window and rollback path.

Expected acceptable debt after this task:

- internal delivery `path` metadata inside pending outputs;
- old pending `svg_map` compatibility normalization;
- diagnostic audit records with internal details;
- compatibility `battle.grid` mirror writes only where old callers still require them.

Expected unacceptable debt after this task:

- ordinary runtime reads from `battle.grid.entities`;
- `scene["last_map_svg"]` as active visual state;
- ordinary map request exposure of `generate_map_svg`;
- SVG/PNG artifact content as backend state input;
- prompt or RA raw grid leakage.

## Rollback / Recovery Plan

Rollback is per atomic commit:

- PRD-only rollback removes this planning document without runtime impact.
- Test-only rollback removes final regression coverage without changing behavior.
- Reader migration rollback restores direct legacy reads only if a replacement helper regression is found.
- Visual fallback rollback restores explicit legacy fallback behavior, not ordinary SVG map routing.
- Docs rollback must match runtime rollback; do not leave docs claiming full rollout if runtime has been reverted.

Old-save recovery:

- Keep tolerant loaders and migration helpers.
- Preserve repository backup/restore behavior.
- If an old shape cannot migrate safely, return a deterministic error or diagnostic summary rather than asking an LLM to infer authority.

## This Task Adds

This task adds:

- final Phase 3 acceptance matrix;
- final legacy deprecation/removal matrix;
- regression coverage for remaining migration seams;
- runtime reader migration away from remaining ordinary `battle.grid` direct reads;
- final visual fallback boundary for `generate_map_svg` and `last_map_svg`;
- docs synchronization for Phase 3 closure.

## Conflicts / Tensions

- Removing old fields reduces complexity, but old saves and external operator habits still need a safe compatibility path.
- Keeping `battle.grid` as a mirror helps old callers, but any ordinary reader can accidentally revive it as authority.
- Keeping `generate_map_svg` as an explicit fallback helps migration, but its current write to `scene["last_map_svg"]` keeps old visual state alive.
- Chat delivery still needs internal local paths, but prompt/player projections must never expose them.
- Large boundary files such as `main.py`, `core/router.py`, and `core/prompts.py` need small, targeted edits or a cohesive split plan before larger changes.

## Out of Scope

- New map gameplay mechanics.
- New renderer features.
- New map editor, UI, or preview subsystem.
- New global coordinate system.
- Broad database migration framework.
- Reopening MapStore, renderer, projection, lifecycle, or cadence architecture without concrete regression evidence.
- Treating SVG, PNG, or visual layout output as map facts.

## Purpose And Means Alignment

Purpose:

- Finish Phase 3 by proving the new map module is fully rolled out and old map authority paths are deprecated.
- Preserve old-save continuity.
- Remove ordinary legacy readers and active legacy visual-state writes.
- Make remaining compatibility explicit and test-covered.

Means:

- Start with a public PRD and evidence matrix.
- Add regression tests before or alongside runtime reader migration.
- Move ordinary readers to code-owned MapStore helpers and role projections.
- Keep compatibility only where evidence shows old-save or delivery need.
- Commit each atomic unit separately.

Trade-offs:

- The final sweep may leave small internal compatibility surfaces, but only if they are non-authoritative and documented.
- The work will touch several large files. Any code edit must be targeted and line-count aware.
- Fully removing `battle.grid` physical mirrors may be unsafe in this task if old saves or tool compatibility still need them; fully removing ordinary reader authority is mandatory.

## Agent-Code Responsibility Split

Code owns:

- MapStore authority selection;
- old-save migration;
- lifecycle transitions;
- spatial calculation routing;
- prompt and RA projection filtering;
- renderer input validation;
- visual metadata writing;
- delivery cadence and pending-output normalization;
- audit and rollback behavior.

Agents or LLMs may own:

- semantic inventory and summarization;
- future bounded candidate map event proposals;
- narrative wording over already-projected map facts.

Agents or LLMs must not own:

- choosing whether `battle.grid` overrides MapStore;
- applying state patches;
- parsing SVG/PNG as map facts;
- hiding hidden facts from raw input;
- deciding delivery cadence;
- deciding combat lifecycle from prompt text;
- validating movement, line of sight, range, topology, or strict grid authority.

## Atomic Commit Plan

| Commit | Purpose | Includes | Excludes | Depends on | Validation |
| --- | --- | --- | --- | --- | --- |
| 1 | Define final sweep PRD and acceptance matrix | This PRD and final matrix | Runtime code changes | pushed 03.1.08 predecessor | `git diff --check`, privacy scan |
| 2 | Add final migration regression coverage | tests for direct readers, stale mirrors, snapshot whitelist, schema/cadence edge cases | behavior changes not needed to make tests compile | commit 1 | targeted pytest collection or focused test files |
| 3 | Route remaining ordinary runtime readers through MapStore | `main.py`, `memory_tools.py`, `core/models.py`, `core/prompts.py` as needed | visual fallback cleanup | commit 2 | targeted map/prompt/memory/main tests |
| 4 | Finalize legacy visual metadata boundary | `generate_map_svg`, `last_map_svg`, render refs / pending metadata policy | new renderer features | commit 3 | registry, prompts, projection, delivery, renderer tests |
| 5 | Final docs and closure report | docs listed in finalization plan | new runtime behavior | commits 2-4 | targeted suite, `git diff --check`, compile check |

## Work Rounds / Commit Checkpoints

| Round | Complete when | Validate with | Commit immediately? | Next round starts after |
| --- | --- | --- | --- | --- |
| 1 | PRD and matrix are committed | `git diff --check`; privacy scan | Yes | clean status after docs commit |
| 2 | regression tests define final expected behavior | focused pytest for touched tests | Yes | clean status after test commit |
| 3 | ordinary runtime readers no longer use `battle.grid` authority | focused tests for main/memory/prompts/models/map paths | Yes | clean status after reader migration commit |
| 4 | legacy visual fallback cannot act as active map state | registry/prompt/projection/delivery/renderer tests | Yes | clean status after visual fallback commit |
| 5 | public docs match final runtime state | targeted suite, compile check, diff check | Yes | PR preparation |

## Acceptance Criteria

- 03.1.08 predecessor is pushed and available as stacked PR target.
- Migration acceptance matrix covers stages 03.1.01 through 03.1.08.
- Legacy deprecation/removal matrix covers every known legacy map state, visual artifact, delivery, prompt, and old-save path.
- No ordinary runtime reader treats `battle.grid` as authority when MapStore strict grid exists.
- No ordinary runtime path treats `last_map_svg`, SVG, PNG, pending output, or local artifact path as map truth.
- New MapStore fields are read by real tools, renderers, prompts, RA builders, delivery, diagnostics, and tests.
- Old fields are removed, inert, compatibility-only, or documented residual debt.
- Old saves migrate cleanly or fail with deterministic recovery guidance.
- DM, RA, renderer, and diagnostics consume intended role projections.
- Tests cover storage authority, calculator routing, projection boundaries, renderer output, delivery cadence, old-save compatibility, and final legacy cleanup seams.
- Docs match implementation and do not leave hidden legacy authority claims.

## Verification Plan

Per-round validation:

```powershell
git diff --check
python -m pytest -q <focused test files> -p no:cacheprovider
```

Final targeted validation:

```powershell
python -m pytest -q tests/test_map_core.py tests/test_spatial_tools.py tests/test_turn_tools.py tests/test_router_usage.py tests/test_strict_lifecycle_tools.py tests/test_strict_grid_render_tools.py tests/test_overview_topology_render_tools.py tests/test_map_delivery_cadence.py tests/test_prompt_projection.py tests/test_prompts.py tests/test_tool_registry.py tests/test_cycle_buffer.py tests/test_environment_agent.py tests/test_dm_ack_and_outputs.py -p no:cacheprovider
python -m compileall -q astrbot_plugin_auto_trpg_dm tests
git diff --check
```

If local Windows temp permissions block full-suite or compile cache behavior, rerun focused tests with a writable repo-local temp root and classify the failure separately from business logic.

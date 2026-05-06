# 03.1.08 Legacy Cleanup And Migration Finalization PRD

## Current / Already Done

Stage 03.1 has already moved the map stack from legacy battle-coupled state toward a MapStore-owned architecture.

- 03.1.01 introduced MapStore-style records, role projections, render refs, and hidden-fact projection guardrails.
- 03.1.02 made `battle.grid` a legacy migration source / compatibility mirror instead of the strict map authority.
- 03.1.03 moved `create_grid` writes through MapStore while preserving user-visible tactical behavior.
- 03.1.04 routed strict spatial operations through `MapCalculator` instead of treating `battle.grid` as the only calculation surface.
- 03.1.05 decoupled strict local map lifecycle from combat lifecycle through `battle.map_id` and strict map records.
- 03.1.06 and 03.1.06.01 clarified role projections and consumer boundaries for DM, RA, renderer, diagnostics, tools, tests, and compatibility fields.
- 03.1.07 replaced ordinary map rendering with deterministic renderer paths that consume `player_view`, write visual-only `render_refs`, and reuse map delivery infrastructure without treating SVG/PNG as map facts.
- 03.1.07.04 established delivery cadence, deterministic-first map routing, explicit legacy fallback exposure, old pending-output compatibility, preview path privacy, and prompt projection guards.

The current PR #21 head provides the immediate baseline for this PRD: deterministic renderers are preferred for ordinary map requests, `generate_map_svg` remains available only as explicit legacy/fallback/style/migration output, and old `svg_map` pending records without `render_type` are normalized as visual-only legacy records.

## Prerequisite Status

This PRD depends on the delivery cadence / legacy SVG migration branch from PR #21. The final runtime cleanup should not be merged independently of that branch unless the cleanup branch is rebased onto a `main` that already contains PR #21.

Current local preflight at PRD time:

- Baseline branch: `feat/delivery-cadence-svg-migration`.
- Stacked branch: `feat/legacy-cleanup-migration-finalization`.
- Base commit: `d3d05b5` (`docs(map-delivery): sync cadence migration status`).
- Target repository: `unhumanplane/paotuan`.
- Preferred stacked PR target before PR #21 merges: `feat/delivery-cadence-svg-migration`.
- Preferred target after PR #21 merges and this branch is rebased: `main`.

Blocking conditions for runtime cleanup:

- If PR #21 receives review changes that alter delivery cadence, deterministic-first routing, preview fallback, prompt projection, or legacy SVG exposure, this PRD must be refreshed before implementation.
- If PR #21 is not merged, this branch may still carry PRD and impact-scan docs, but final deletion/downgrade commits should clearly target PR #21 as their base and should be easy to rebase after merge.

## Relationship To PR #21

PR #21 is the last renderer/delivery migration stage. It deliberately keeps several legacy surfaces for compatibility:

- `generate_map_svg` remains implemented as an explicit legacy fallback.
- `scene["last_map_svg"]` remains readable as a safe visual reference for old saves.
- `scene["_pending_outputs"]` still uses `type: "svg_map"` as the chat attachment discriminator.
- Old pending `svg_map` records without `render_type` remain deliverable as `legacy_generate_map_svg` visual-only output.
- Internal artifact paths may remain in delivery metadata, but ordinary prompt/player projections must strip local paths and raw SVG.

03.1.08 is the cleanup/finalization stage after those migration paths exist. It should not reopen renderer architecture, renderer geometry, or delivery cadence design unless implementation evidence proves the PR #21 contract is wrong. Its job is to make legacy fields removed, inert, or explicitly compatibility-only.

## Impact Scan Evidence

The impact scan covers these surfaces:

- `battle.grid`
- `battle.map_id`
- `scene["last_map_svg"]`
- `scene["_pending_outputs"]`
- direct SVG assumptions and `generate_map_svg`
- prompt snapshot keys and prompt projection
- old-save loaders and runtime migration
- tool schemas and tool routing
- audit records and diagnostic surfaces
- tests and inline compatibility fixtures
- docs and staged PRD contracts
- MapStore authority paths
- renderer output metadata
- pending-output map delivery paths
- prompt / RA projection consumers
- old save/session records

Primary implementation files identified by the scan:

- `astrbot_plugin_auto_trpg_dm/core/map_core.py`
- `astrbot_plugin_auto_trpg_dm/core/map_delivery_cadence.py`
- `astrbot_plugin_auto_trpg_dm/core/map_tool_routing.py`
- `astrbot_plugin_auto_trpg_dm/core/models.py`
- `astrbot_plugin_auto_trpg_dm/core/prompt_projection.py`
- `astrbot_plugin_auto_trpg_dm/core/prompts.py`
- `astrbot_plugin_auto_trpg_dm/core/cycle_buffer.py`
- `astrbot_plugin_auto_trpg_dm/main.py`
- `astrbot_plugin_auto_trpg_dm/storage/json_repository.py`
- `astrbot_plugin_auto_trpg_dm/tools/map_tools.py`
- `astrbot_plugin_auto_trpg_dm/tools/registry.py`
- `astrbot_plugin_auto_trpg_dm/tools/spatial_tools.py`
- `astrbot_plugin_auto_trpg_dm/tools/strict_grid_render_tools.py`
- `astrbot_plugin_auto_trpg_dm/tools/overview_topology_render_tools.py`
- `astrbot_plugin_auto_trpg_dm/tools/strict_lifecycle_tools.py`
- `astrbot_plugin_auto_trpg_dm/tools/turn_tools.py`

Primary regression files identified by the scan:

- `tests/test_map_core.py`
- `tests/test_spatial_tools.py`
- `tests/test_strict_lifecycle_tools.py`
- `tests/test_strict_grid_render_tools.py`
- `tests/test_overview_topology_render_tools.py`
- `tests/test_map_delivery_cadence.py`
- `tests/test_prompt_projection.py`
- `tests/test_prompts.py`
- `tests/test_tool_registry.py`
- `tests/test_dm_ack_and_outputs.py`
- `tests/test_cycle_buffer.py`

Primary public docs identified by the scan:

- `docs/mapcore-projection-guard.md`
- `docs/projection-consumer-matrix.md`
- `docs/coordinate-renderer-contract.md`
- `docs/strict-grid-svg-renderer-prd.md`
- `docs/overview-topology-svg-renderer-prd.md`
- `docs/delivery-cadence-legacy-svg-migration-prd.md`

## Legacy Field Removal Matrix

| Field/path | Current readers | Current writers | Replacement | Compatibility window | Old-save behavior | Rollback path | Required tests | 03.1.08 action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `battle.grid` legacy mirror | Strict-grid loader, spatial tools, strict lifecycle, turn/local fast paths, memory/status helpers, raw battle snapshot tests | `create_grid`, spatial `_save_grid`, strict lifecycle start/resume, legacy grid migration, strict renderer migration side effect | `session.maps.records[active_strict_map_id].grid` plus `battle.map_id` combat link | Keep only as old-save loader or narrow compatibility mirror until all normal readers/writers are moved | If MapStore strict grid is absent, wrap/migrate old `battle.grid` into `strict_local_map.grid`; if MapStore exists, legacy mirror must not override it | Restore mirror reads/writes while keeping MapStore precedence | `test_map_core.py`, `test_spatial_tools.py`, `test_strict_lifecycle_tools.py`, `test_strict_grid_render_tools.py` | Move normal readers/writers off mirror where feasible; leave explicit old-save compatibility only. Any residual mirror writer must be listed for 03.1.08.01 audit. |
| `battle.map_id` | combat lifecycle, strict lifecycle, spatial tools, render tools, turn/status helpers | `create_grid`, `start_combat_on_map`, strict lifecycle link/unlink, legacy migration | Keep as combat-to-strict-map link | Permanent bridge unless later architecture replaces combat link state | Old combat state may link to migrated strict map | Restore previous combat link update logic | strict lifecycle and spatial tests | Keep. Not a legacy cleanup target. |
| `scene["last_map_svg"]` | prompt scene projection safe-ref path, old save compatibility tests | legacy `generate_map_svg` | MapStore `render_refs` and normalized pending output metadata | Read compatibility remains; new deterministic outputs should not write this as active state | Safe-project old value as visual metadata only; never use as map facts | Restore safe projection or fallback alias | `test_prompts.py`, `test_prompt_projection.py`, map delivery tests | Downgrade to historical/compat metadata and stop treating it as an active render contract. Remove or narrow new writes if replacement path is ready. |
| `generate_map_svg` tool | explicit fallback route, registry, prompt guidance, tests | writes SVG file, `last_map_svg`, `_pending_outputs`, audit | `render_strict_grid_svg` and `render_overview_topology_svg` over `player_view` | Explicit legacy/fallback/style/migration window only; no ordinary map request exposure | Old sessions do not need the tool, but old operator workflows may use fallback during migration | Restore explicit fallback exposure; do not restore ordinary exposure without PRD update | `test_tool_registry.py`, `test_prompts.py`, `test_prompt_projection.py` | Finalize as migration-only or diagnostic fallback, or remove normal schema exposure entirely if tests prove deterministic paths cover ordinary use. Do not keep open-ended fallback without window. |
| direct SVG as backend fact source | legacy prompt/tool assumptions and possible downstream misuse | legacy LLM SVG output | structured MapStore facts, `player_view` renderer envelope, render refs | No compatibility window for authority use | Old SVG remains visual artifact only | Restore visual-only fallback, never authority behavior | renderer no-writeback and projection tests | Remove obsolete prompt/tool assumptions that treat SVG/PNG as map facts or backend input. |
| `scene["_pending_outputs"]` | `main._pop_pending_outputs`, chat result attachment, cadence filter, prompt projection drop list | render tools via cadence, legacy map tool, other output producers | delivery queue infrastructure with normalized metadata | Keep while chat attachment path depends on it | Old queue records are normalized or dropped safely | Restore previous pop path if cadence breaks delivery | `test_map_delivery_cadence.py`, `test_dm_ack_and_outputs.py`, prompt tests | Keep as delivery infrastructure, not map state. Ensure it is not projected to DM/RA/player facts. |
| `type: "svg_map"` pending discriminator | chat delivery attachment loop and old pending compatibility | legacy and deterministic map delivery producers | keep discriminator; use `render_type` for renderer identity | Keep until chat delivery has a replacement discriminator | Old records without `render_type` map to legacy visual-only | Restore old discriminator handling | map delivery and DM output tests | Keep. Not a cleanup target in 03.1.08. |
| pending metadata `render_type`, `preferred_render_type`, `visual_only`, `delivery_*`, `cadence_key` | cadence filter, prompt/tool projection tests, docs | `enqueue_map_pending_output`, render tools | normalized map delivery contract | Permanent v1 delivery contract | Missing `render_type` records normalize to `legacy_generate_map_svg` | Restore legacy default normalization | `test_map_delivery_cadence.py`, `test_prompt_projection.py` | Strengthen and verify; prevent legacy default from becoming new writer behavior. |
| MapStore `render_refs[*]` | projected map views, renderer tests, prompt projection | render tools through `add_render_ref` | safe projected render references | Permanent replacement for active `last_map_svg` | Old refs missing metadata should project conservatively | Restore render ref projection | `test_map_core.py`, `test_prompt_projection.py`, renderer tool tests | Keep and document as canonical visual metadata. |
| internal render ref `path` / `url` | delivery/debug internals only | render tools and legacy map tool | safe projected ref with `type`, `title`, `name`, `visual_only`, revisions | Internal only | Old paths may remain internally but must not project | Restore path stripping rules | prompt projection and DM output tests | Keep internal, strip from ordinary prompt/player output. |
| prompt snapshot `last_map_svg` | `prompt_snapshot_data` scene projection | old scene records | safe render ref or projected maps | Read compatibility only | Old value projects without path/raw SVG | Restore `_project_map_ref` | `test_prompts.py` | Keep only as old-save visual metadata. |
| prompt snapshot `_pending_outputs` / `_map_delivery_cadence` | should not be ordinary prompt readers | delivery infrastructure | projection drop list | No ordinary prompt compatibility needed | silently dropped from prompt projection | restore drop list | `test_prompts.py` | Keep blocked and test. |
| prompt/tool result raw path/SVG/layout/cadence keys | projection filters | tool results, delivery metadata, render results | prompt-safe tool result projection | No prompt/player compatibility | old tool results are projected conservatively | restore blocked key list | `test_prompt_projection.py` | Keep blocked and expand tests if new cleanup touches these paths. |
| `get_battle_snapshot()` raw `battle` / `grid.to_dict()` | tactical toolsets, state query paths, tests, DM tool result projection | returns current session battle and grid | safe battle/status projection plus diagnostic-only raw snapshot if needed | Must be resolved by 03.1.08 or explicitly listed for 03.1.08.01 with owner and reason | Old callers may expect raw shape; migration requires compatibility wrapper or diagnostic escape hatch | restore raw tool behavior | `test_spatial_tools.py`, `test_tool_registry.py`, `test_prompt_projection.py` | Candidate cleanup target. Do not leave as open-ended future debt. Narrow ordinary exposure or add safe projection. |
| RA tool-result raw grid exposure | RA cycle input sanitizer and action trace paths | tool results captured into cycle action buffers | RA authority projection plus sanitizer guard | Must be verified in 03.1.08; residual only if scan proves no reachable unsafe path | Old buffers should sanitize or ignore raw grid | restore sanitizer allowlist while keeping prompt/player guards | `test_cycle_buffer.py`, RA/environment tests | Verify and fix if reachable. If not changed, record as explicit 03.1.08.01 audit item. |
| audit records containing map artifact paths/results | diagnostic and repository audit readers | map tools, render tools, router/main audit writes | diagnostic-only audit records; prompt/player projection must never consume raw audit | Keep diagnostic only | old audit remains readable but non-authoritative | restore audit write shape | audit/projection tests | Keep internal. Add final migration audit entries/tests where cleanup changes behavior. |
| old saves missing `maps` | `GameSession.from_dict`, repository load | old saved sessions | `default_map_store()` and runtime migration helpers | Permanent tolerant reader | load cleanly; migrate strict grid on first strict map use | restore tolerant loader | `test_map_core.py`, repository/model tests | Keep loader. |
| old saves with stale `_pending_outputs` | pending pop/filter path | old sessions | cadence normalization and safe drop/attach | Keep until old session queue risk is exhausted | normalize visual map records; drop unsupported or unsafe records | restore pre-cadence pop path | `test_map_delivery_cadence.py`, `test_dm_ack_and_outputs.py` | Keep compatibility but ensure no authority leakage. |
| docs/spec references to legacy map fields | maintainers and future PRDs | docs updates | final docs contract | Must match implementation by 03.1.08.01 | old docs should not define authority | revert docs only if code rollback | docs review and `git diff --check` | Update as cleanup lands; no stale undocumented paths. |

## Old Save Migration Plan

The project does not use a broad database migration framework. Saved state migration should be narrow, testable, and tolerant.

Rules:

1. Do not perform broad in-place rewrite of all sessions unless a concrete cleanup requires it.
2. Keep `GameSession.from_dict()` tolerant for missing `maps`, old `battle`, old `scene`, and old visual records.
3. When strict map operations need a grid and MapStore has no active strict grid, wrap legacy `battle.grid` into a `strict_local_map` record with auditable migration metadata.
4. When MapStore already has an active strict grid, stale `battle.grid` must not overwrite MapStore authority.
5. Old `scene["last_map_svg"]` records project as historical visual metadata only.
6. Old pending `svg_map` records without `render_type` normalize to `legacy_generate_map_svg` and `visual_only`.
7. If a cleanup makes an old shape impossible to migrate safely, fail with a clear recovery message and preserve repository backup/restore behavior.

## MapStore Authority Guard

MapStore is the authoritative map state for strict map grids and future map facts. Legacy `battle.grid` has only three allowed roles after cleanup:

- old-save migration source;
- temporary compatibility mirror for a proven remaining old caller;
- diagnostic evidence of legacy state, not authority.

Forbidden behavior:

- stale `battle.grid` changes movement, line of sight, range, rendering, or topology when MapStore strict grid exists;
- renderer reads raw `battle.grid` as input;
- ordinary DM prompt receives raw strict grid;
- RA or DM output directly patches `maps` or `battle.grid` without code validation;
- SVG/PNG output writes coordinates or facts back into MapStore.

## Visual-Only Render Metadata Plan

The canonical visual metadata path is MapStore `render_refs` plus normalized pending-output metadata.

`scene["last_map_svg"]` should not be a live map-render contract after 03.1.08. It may remain only as old-save/historical compatibility, with safe fields such as type, title, name, and `visual_only`.

Render metadata rules:

- deterministic renderers write `render_refs` on the source map record;
- deterministic renderers enqueue `type: "svg_map"` pending records with `render_type`, `visual_only`, map revision, layout revision, delivery trigger, and cadence metadata;
- internal `path` may exist for local delivery and debug, but projection strips it from ordinary prompt/player output;
- raw SVG is not map state;
- visual output never becomes movement, line-of-sight, topology, coordinate, or visibility authority.

## Prompt / Tool Schema Cleanup Plan

03.1.08 should close obsolete prompt and tool assumptions, not only describe them.

Required cleanup direction:

- ordinary map requests expose deterministic renderers first;
- `generate_map_svg` remains unavailable for normal map requests;
- explicit legacy/fallback/style/migration exposure must have a documented compatibility window;
- prompt text must not imply SVG can create map facts;
- prompt snapshot projection must continue to block `_pending_outputs`, `_map_delivery_cadence`, local paths, raw SVG, layout internals, and raw strict grid;
- `get_battle_snapshot()` must either move toward a safe ordinary battle/status projection or be clearly restricted as diagnostic/authority-only.

## Audit / Regression Plan

Audit records remain internal. They can preserve details needed for debugging and migration evidence, but they must not become ordinary prompt/player facts.

Required regression coverage:

- old save without `maps` loads with default MapStore;
- old save with only `battle.grid` migrates on strict map access;
- stale legacy mirror cannot override MapStore authority;
- `create_grid`, `move_entity`, and `check_attack_vector` use MapStore-backed strict grid behavior;
- strict lifecycle links combat through `battle.map_id` and does not destroy strict map records on combat end;
- deterministic renderers write visual-only refs and do not write rendered SVG back into map facts;
- old `last_map_svg` projects as safe historical metadata only;
- old `svg_map` pending record without `render_type` normalizes as visual-only legacy output;
- local paths, raw SVG, cadence keys, layout internals, hidden facts, and raw grid do not enter ordinary prompt/player projections;
- legacy fallback exposure remains explicit-only or is removed with a tested compatibility plan;
- RA/cycle input does not receive raw strict grid through ordinary tool-result capture.

## Rollback Plan

Rollback must be possible per atomic commit.

- PRD-only commit rollback removes planning docs without runtime impact.
- MapStore authority cleanup rollback restores the previous compatibility mirror behavior while keeping old-save loader tests visible.
- Tool/schema cleanup rollback restores the previous explicit fallback exposure, not ordinary legacy map exposure unless explicitly required.
- Visual metadata cleanup rollback restores `last_map_svg` write compatibility while keeping projection guards.
- Projection cleanup rollback restores prior sanitizer/projection behavior while keeping blocked local path/raw SVG tests as review signals.
- Docs/tests rollback must not hide runtime behavior drift; if runtime is rolled back, docs must roll back in the same follow-up.

## This Task Adds

This task adds a PRD, impact-scan evidence, and a removal matrix that binds the final map cleanup stage to concrete runtime cleanup work.

The PRD is intentionally stricter than a passive audit. It requires each legacy field to be removed, made inert, or explicitly limited to compatibility-only behavior. Deferral is allowed only with a concrete blocker, a compatibility window, an owner stage, and 03.1.08.01 audit coverage.

## Conflicts / Tensions

- Removing legacy fields reduces complexity, but old saves still need continuity.
- Keeping compatibility mirrors helps old callers, but mirrors must not remain authority paths.
- Hiding `generate_map_svg` improves map truth stability, but an explicit fallback may still be useful during staged migration.
- `get_battle_snapshot()` is useful for tactical state queries, but its raw shape conflicts with projection safety.
- Internal delivery paths need local file paths, but prompt/player outputs must never expose local paths.
- PR #21 is still the base for this cleanup branch; review changes in PR #21 may alter cleanup assumptions.

## Out of Scope

- New gameplay mechanics.
- New renderer features.
- New map editor or preview subsystem.
- Reopening settled MapStore, projection, renderer, or cadence architecture without concrete implementation evidence.
- Treating SVG/PNG as map facts or backend input.
- Replacing the whole chat delivery system.
- Broad database migration framework.

## Purpose And Means Alignment

Purpose:

- Finish the staged map migration by closing obsolete legacy surfaces.
- Preserve old-save continuity and player-facing behavior.
- Make MapStore the clear authority for map state.
- Ensure visual outputs are delivery metadata only.
- Leave stage 3 ready for the final 03.1.08.01 whole-map-system acceptance audit.

Means:

- Start with this removal matrix and impact scan.
- Move normal readers/writers off legacy surfaces before deleting or making fields inert.
- Keep only narrow old-save loaders or compatibility shims with explicit tests.
- Add regression tests for cleanup and rollback-sensitive paths.
- Sync public docs with actual behavior.
- Defer only items with explicit blocker evidence, compatibility windows, and audit ownership.

Trade-offs:

- Some fields may remain physically present for compatibility, but they must lose authority semantics.
- Runtime cleanup is split into several reviewable commits instead of one sweeping deletion.
- 03.1.08.01 will still audit the final state, but 03.1.08 must do actual cleanup work rather than hiding all remaining migration behind the audit.

## Agent-Code Responsibility Split

Code-owned responsibilities:

- state authority;
- migration loaders;
- compatibility windows;
- tool routing;
- schema validation;
- projection filtering;
- fallback permission;
- audit boundary;
- persistence and recovery;
- deterministic rendering and delivery metadata;
- tests and rollback behavior.

Agent/LLM responsibilities:

- semantic inventory of legacy surfaces;
- structured cleanup recommendations;
- PRD wording;
- candidate map/event interpretation in future workflows, subject to code validation.

Do not delegate these to prompts or agents:

- deciding whether legacy fields override MapStore;
- applying state patches;
- routing ordinary map requests to fallback tools;
- determining old-save migration success;
- hiding local paths or raw SVG;
- managing delivery cadence;
- validating movement, distance, line of sight, visibility, or topology.

## Atomic Commit Plan

| Commit | Purpose | Includes | Excludes | Depends on | Validation |
| --- | --- | --- | --- | --- | --- |
| 1 | Add PRD and removal matrix | This docs PRD, impact scan, cleanup plan, acceptance criteria | Runtime code changes | PR #21 head | `git diff --check`; privacy scan |
| 2 | Move normal strict-grid authority off legacy mirror | MapStore/legacy grid reader-writer cleanup, old-save migration guard tests | prompt/tool schema cleanup, renderer changes | commit 1 | `tests/test_map_core.py`, `tests/test_spatial_tools.py`, `tests/test_strict_lifecycle_tools.py`, `tests/test_strict_grid_render_tools.py` |
| 3 | Narrow raw battle and legacy map tool schemas | `get_battle_snapshot` ordinary/diagnostic boundary, `generate_map_svg` exposure/window cleanup | renderer core changes | commit 2 | `tests/test_tool_registry.py`, `tests/test_spatial_tools.py`, `tests/test_prompt_projection.py`, `tests/test_prompts.py` |
| 4 | Finalize visual-only metadata replacement | `last_map_svg` downgrade/removal of new writes, render ref / pending metadata compatibility | MapStore authority changes | commit 3 | `tests/test_prompts.py`, `tests/test_prompt_projection.py`, `tests/test_map_delivery_cadence.py`, renderer tool tests |
| 5 | Close prompt / RA legacy leakage paths | prompt projection, tool-result projection, RA sanitizer or verified non-issue tests | new gameplay or renderer features | commit 4 | `tests/test_prompt_projection.py`, `tests/test_cycle_buffer.py`, RA/environment tests |
| 6 | Sync docs, audit, and final regression notes | docs updates, audit/rollback notes, high-level regression matrix | new feature work | commits 2-5 | targeted suite, `python -m compileall -q astrbot_plugin_auto_trpg_dm tests`, `git diff --check` |

## Work Rounds / Commit Checkpoints

| Round | Complete when | Validate with | Commit immediately? | Next round starts after |
| --- | --- | --- | --- | --- |
| 1 | PRD and matrix are reviewed and accepted | `git diff --check`; privacy scan | Yes | clean status after docs commit |
| 2 | normal strict-grid paths no longer depend on legacy mirror authority | targeted map/spatial/lifecycle tests | Yes | clean status after authority cleanup commit |
| 3 | raw battle and legacy map tool exposure has explicit final behavior | tool registry, spatial, prompt tests | Yes | clean status after schema cleanup commit |
| 4 | `last_map_svg` is historical/compat only and render refs are canonical | prompt/projection/cadence/renderer tests | Yes | clean status after metadata cleanup commit |
| 5 | prompt/RA/tool-result leakage paths are closed or explicitly audited | projection and RA tests | Yes | clean status after projection cleanup commit |
| 6 | docs, audit, rollback, and regression coverage match implementation | targeted suite, compileall, diff check | Yes | PR preparation |

## Acceptance Criteria

- Removal matrix covers all known legacy map/spatial fields and delivery surfaces.
- Each matrix row states whether the field is removed, made inert, kept compatibility-only, or reserved for 03.1.08.01 audit with explicit blocker evidence.
- Old saves either migrate cleanly or fail with clear recovery guidance.
- MapStore remains authoritative for map state wherever previous stages said it should be.
- Legacy mirrors cannot overwrite map authority.
- `battle.grid` is no longer a normal authority path after cleanup; any remaining use is old-save or explicitly compatibility-only.
- `scene["last_map_svg"]` is historical/compat metadata only, not active map state.
- `generate_map_svg` is not an ordinary map route and has either a removal path or a bounded explicit fallback window.
- Visual outputs are render/delivery metadata only.
- Prompt/player/ordinary RA consumers do not receive raw strict grid, raw SVG, local paths, hidden facts, cadence internals, or layout internals.
- Tests cover compatibility, cleanup, and rollback-sensitive paths.
- Public docs match the final implemented state.
- 03.1.08.01 receives an explicit audit matrix for any remaining intentional legacy debt.

## Verification Plan

PRD-only verification:

```bash
git diff --check
git status --short --branch
```

Runtime cleanup verification will be selected by commit scope, then finalized with:

```bash
python -m pytest -q tests/test_map_core.py tests/test_spatial_tools.py tests/test_strict_lifecycle_tools.py tests/test_strict_grid_render_tools.py tests/test_overview_topology_render_tools.py tests/test_map_delivery_cadence.py tests/test_prompt_projection.py tests/test_prompts.py tests/test_tool_registry.py tests/test_dm_ack_and_outputs.py -p no:cacheprovider
python -m pytest -q tests/test_cycle_buffer.py -p no:cacheprovider
python -m compileall -q astrbot_plugin_auto_trpg_dm tests
git diff --check
```

If local pytest infrastructure hits Windows permission errors under runtime temp directories, separate environment failures from business logic failures and rerun targeted tests with an isolated writable temp root.

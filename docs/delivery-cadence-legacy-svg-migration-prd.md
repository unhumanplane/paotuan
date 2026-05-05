# Delivery Cadence And Legacy SVG Migration PRD

## Current / Already Done

Current `main` already has the two deterministic renderer paths needed for this
migration:

- PR #19 landed the strict-grid SVG renderer on `main`.
- PR #20 landed the overview-topology SVG renderer on `main`.
- Current `upstream/main` is `c74e38f`, with first-parent history showing PR #20
  after PR #19.
- `render_strict_grid_svg` and `render_overview_topology_svg` are both available
  from the tool registry.
- Both renderer tool paths write visual-only render refs and can enqueue
  `_pending_outputs` records using `type: "svg_map"` plus renderer-specific
  `render_type` metadata.
- The legacy `generate_map_svg` path still exists, still uses an LLM sub-context
  to write SVG, and still writes `scene["last_map_svg"]` plus pending output
  records.

Recommended branch strategy for this task is to start from current
`upstream/main` after PR #19 and PR #20, using a feature branch such as
`feat/delivery-cadence-svg-migration`. This task should not be stacked on either
renderer feature branch now that both have merged.

This PRD is for delivery cadence, deterministic-first map routing, legacy
LLM-written SVG migration, and compatibility policy. It is not a renderer
geometry or topology-layout PRD.

## Prerequisite Status

| Prerequisite | Status | Evidence | Effect on this task |
| --- | --- | --- | --- |
| Renderer input boundary | Done. | `docs/coordinate-renderer-contract.md` and `docs/projection-consumer-matrix.md` define `player_view + render request/envelope` in and render refs / delivery metadata out. | Consume the boundary; do not redefine it here. |
| Strict-grid renderer | Done on `main`. | `docs/strict-grid-svg-renderer-prd.md`, `rendering/strict_grid_svg.py`, `rendering/strict_grid_adapter.py`, `tools/strict_grid_render_tools.py`, and tests. | Strict or tactical map delivery can route to `render_strict_grid_svg` when a player-view strict map exists. |
| Overview-topology renderer | Done on `main`. | `docs/overview-topology-svg-renderer-prd.md`, `rendering/overview_topology.py`, `tools/overview_topology_render_tools.py`, and tests. | Non-combat overview delivery can route to `render_overview_topology_svg` when player-visible topology exists. |
| Existing SVG/PNG delivery | Present but legacy-shaped. | `main.py` pops `_pending_outputs`, handles `svg_map`, and renders PNG previews. | Reuse where safe; harden player-facing fallback behavior where unsafe. |
| Legacy LLM SVG path | Present. | `tools/map_tools.py` implements `generate_map_svg`. | Downgrade to temporary fallback / style experiment / migration-only path. |

## Relationship To Strict Grid Renderer

The strict-grid renderer is the deterministic path for tactical, exploration,
combat, puzzle, or other strict local maps.

This task should:

- route renderable strict-map requests and strict cadence triggers to
  `render_strict_grid_svg`;
- preserve the renderer contract that the strict adapter uses `player_view`;
- preserve `visual_only` render refs and `_pending_outputs` compatibility;
- add cadence state outside the renderer core;
- avoid adding cadence decisions to `rendering/strict_grid_svg.py`, whose job is
  deterministic drawing only;
- avoid changing strict grid geometry, rule-scale rendering, or hidden-cell
  masking in this task.

The strict renderer already proves that legacy-only `battle.grid` can be
migrated for compatibility without calling `generate_map_svg`. Delivery routing
must not treat `battle.grid`, rendered SVG, PNG output, or local artifact paths
as map authority.

## Relationship To Overview Topology Renderer

The overview-topology renderer is the deterministic path for player-facing
non-combat overview maps: discovered places, routes, area relationships,
landmarks, and current location.

This task should:

- route renderable overview requests and major non-combat transition triggers to
  `render_overview_topology_svg`;
- preserve the renderer contract that overview input is built from
  `player_view`;
- preserve layout-cache behavior without treating layout output as map facts;
- keep `overview_topology_missing` as a stable signal that deterministic overview
  output is unavailable;
- decide whether a missing overview can use legacy fallback only through an
  explicit migration/fallback policy;
- avoid changing overview topology layout, node placement, edge rendering, or
  hidden-topology guards in this task.

Overview and strict-grid delivery should share cadence and output-delivery
policy, not renderer-specific layout logic.

## Impact Scan Evidence

| Area | Current evidence | Migration impact |
| --- | --- | --- |
| Tool registry exposure | `tools/registry.py:332` registers `render_strict_grid_svg`; `tools/registry.py:350` registers `generate_map_svg`; `tools/registry.py:356` registers `render_overview_topology_svg`; `_with_llm_decided_tools()` still appends `generate_map_svg` at `tools/registry.py:627`. | Normal map requests need deterministic-first exposure. Legacy `generate_map_svg` should be hidden from normal tools except explicit fallback / migration modes. |
| Legacy `generate_map_svg` | `tools/map_tools.py:47` defines the tool, `tools/map_tools.py:101` calls `MAP_SYSTEM_PROMPT`, `tools/map_tools.py:121` sanitizes SVG, `tools/map_tools.py:156` writes `scene["last_map_svg"]`, and `tools/map_tools.py:158` appends `_pending_outputs`. | Keep as temporary visual-only fallback. Do not use as normal map routing when deterministic renderer output is available. |
| Renderer tools | `tools/strict_grid_render_tools.py:54` projects `MAP_VIEW_PLAYER`, `tools/strict_grid_render_tools.py:72` adds `strict_grid_svg` render refs, and `tools/strict_grid_render_tools.py:82` emits pending `svg_map`; `tools/overview_topology_render_tools.py:58` projects `MAP_VIEW_PLAYER`, `tools/overview_topology_render_tools.py:133` adds render refs, and `tools/overview_topology_render_tools.py:142` emits pending `svg_map`. | Both renderer tools are valid delivery producers. Cadence should orchestrate when to call them, not change renderer cores. |
| `scene["last_map_svg"]` | `core/prompts.py:251` projects it through `_project_map_ref`; `core/prompts.py:271` excludes raw `last_map_svg` from ordinary scene projection; `tools/map_tools.py:156` still writes it. | Keep old records as legacy visual references. Do not read it as topology, grid, coordinate, or delivery-cadence authority. Future cleanup can retire it after deterministic paths stabilize. |
| `_pending_outputs` | `main.py:369` and `main.py:490` pop pending outputs for replies; `main.py:2668` defines `_pop_pending_outputs`; renderer tools and `map_tools.py` append to the scene list. | Reuse the queue shape for v1, but require explicit cadence state so rendering does not spam. Queue records remain delivery-only metadata. |
| SVG/PNG preview conversion | `main.py:2389` only attaches pending records whose type is `svg_map`; `main.py:2394` calls `_ensure_png_preview`; `main.py:2448` renders PNG preview. | Reuse preview conversion. Harden fallback so player chat does not print local artifact paths if preview rendering fails. |
| Chat send hooks | `main.py:370` sends fast replies with pending outputs; `main.py:505` sends completion replies with non-dice outputs; `main.py:637`, `main.py:1870`, and `main.py:2144` use direct `send_message` paths. | Cadence integration should target the normal DM result path first. Direct send paths need review before automatic map sends are added there. |
| Prompt instructions | `core/prompts.py:776` already prefers overview deterministic rendering when structured overview topology exists; `core/prompts.py:1001` still prefers `generate_map_svg` for ordinary visual maps; `core/prompts.py:1006` repeats overview-first guidance. | Update prompts to deterministic-first across supported map families. Legacy fallback must be described as fallback, not normal SVG generation. |
| Audit output | `tools/map_tools.py:173`, `tools/strict_grid_render_tools.py:117`, and `tools/overview_topology_render_tools.py:176` audit tool results. | Keep audit records. Audit may include internal artifact metadata, but audit output must not become ordinary prompt/player fact input. |
| Tests | Existing coverage includes `tests/test_tool_registry.py`, `tests/test_prompt_projection.py`, `tests/test_prompts.py`, `tests/test_strict_grid_render_tools.py`, `tests/test_overview_topology_render_tools.py`, and renderer unit tests. | Add tests for cadence state, deterministic-first routing, legacy fallback exposure, no local path chat leakage, old-record compatibility, and no SVG-to-fact writeback. |
| Old-save/session records | Old sessions may contain `scene["last_map_svg"]`, stale `_pending_outputs`, legacy `battle.grid`, or render refs without `render_type`. | Do not migrate old sessions in place. Treat missing cadence state as empty v1 state, keep safe legacy refs visible only as render refs, and ignore or safely clear stale pending outputs when needed. |
| Player-view render outputs | `core/map_core.py:9` defines `MAP_VIEW_PLAYER`; `core/map_core.py:597` projects render refs to safe fields only; `core/prompt_projection.py:6` blocks prompt-unsafe keys such as `file_path`, `layout`, `layout_updates`, `raw_svg`, `svg`, and `url`. | Delivery must consume player-view renderer outputs. Internal paths are allowed only inside delivery metadata, not prompt facts. |
| Legacy LLM-written SVG entrypoints | `MAP_SYSTEM_PROMPT` at `tools/map_tools.py:251` tells the SVG sub-agent to output complete SVG; `_build_map_prompt()` at `tools/map_tools.py:270` builds legacy prompt context. | Keep only inside explicit legacy fallback. Do not let normal deterministic routing call this path first. |
| File size risk | `main.py` is 4435 lines, `map_tools.py` is 1322 lines, `registry.py` is 1298 lines, and `core/prompts.py` is 1267 lines. | Future implementation should add cohesive delivery/cadence logic in a responsibility-based module and keep edits to these files as narrow integration hooks. |

## Legacy SVG Entrypoint Matrix

| Entrypoint | Current role | Treatment | Reason |
| --- | --- | --- | --- |
| `generate_map_svg` tool | Legacy LLM-written SVG generator and chat delivery producer. | Kept as temporary fallback / style experiment / migration-only path; hidden from normal deterministic-capable map tools. | It is useful for compatibility but should not be the normal renderer after strict and overview deterministic paths exist. |
| `GenerateMapSvgArgs` | Free-form legacy SVG request schema. | Kept for fallback only. | It is prompt-oriented and not the deterministic renderer envelope. |
| `MAP_SYSTEM_PROMPT` | Tells a sub-agent to output SVG. | Kept inside legacy fallback only. | Removing immediately risks compatibility; normal rendering should not use it. |
| `_build_map_prompt()` | Builds legacy LLM SVG prompt from scene/battle context. | Kept inside legacy fallback only; removed or narrowed in later cleanup. | It may still support fallback, but must not become topology/grid authority. |
| `sanitize_svg()` | Sanitizes generated SVG before file write. | Reused where safe, especially for fallback and any future externally authored SVG. | Deterministic renderers emit safe SVG subsets, but sanitizer remains useful compatibility infrastructure. |
| Legacy file writing in `map_tools.py` | Writes SVG artifacts for `generate_map_svg`. | Kept as fallback implementation; not used by deterministic renderers unless a shared helper is factored later. | Avoid making `map_tools.py` larger during cadence work. |
| `scene["last_map_svg"]` | Legacy visual reference projected safely by prompts. | Intentionally unchanged for read compatibility; no new deterministic dependency. Later cleanup target. | Old sessions may contain it, but it is not map truth. |
| Legacy `_pending_outputs` record with no `render_type` | Delivery queue item for old SVG maps. | Kept as delivery-only compatibility; not a fact source. | Old pending records and fallback output should still attach where safe. |
| `type: "svg_map"` delivery discriminator | Shared attachment compatibility key. | Intentionally reused. | Existing chat delivery and tests depend on it. Renderer identity should live in `render_type`. |
| Plain local path fallback in chat | Current fallback when PNG preview fails. | Change during implementation to avoid exposing local paths to player chat. | Internal artifact paths are delivery metadata and should not be player-facing. |
| Prompt rule that prefers `generate_map_svg` for ordinary maps | Normal map guidance for legacy behavior. | Update to deterministic-first; keep fallback wording only when deterministic rendering is unavailable or explicitly allowed. | Current text conflicts with the post-renderer migration goal. |
| Registry default that appends `generate_map_svg` | Normal tool exposure fallback. | Replace with explicit routing/fallback policy. | The default keeps legacy SVG normal even after deterministic renderers exist. |
| Audit records for legacy SVG | Tool audit of inputs/results. | Intentionally unchanged with projection guard; review for raw path export if audit is surfaced later. | Audit is useful for debugging but is not player/backend map authority. |
| Old session `last_map_svg` / old render refs | Compatibility data from previous saves. | Intentionally unchanged; project only safe fields and ignore as renderer input. | Avoid in-place save migration for a delivery PR. |

## Delivery Cadence Contract

Delivery is a player-facing side effect. It does not create map facts, rewrite
coordinates, select hidden facts, or feed backend adjudication.

V1 trigger policy:

| Trigger | Default map family | Cadence behavior |
| --- | --- | --- |
| Player explicitly asks for a map | Strict or overview, based on current player-view renderability and request intent. | Overrides cadence/cooldown if a player-view map can be rendered. |
| Major non-combat scene transition | Overview map. | Send once per transition identity / map revision when player-visible overview topology exists. |
| Strict exploration start | Strict grid. | Send when the active strict map becomes relevant and has player-visible strict data. |
| Strict exploration end | Strict grid or no-send summary, depending on current strict map state. | Send only if it helps preserve player spatial understanding and has not already been sent for the same end marker. |
| Combat start | Strict grid. | Send once for the combat start map/revision. |
| Combat end | Strict grid or overview, depending on resulting scene state. | Send once for the end marker if renderable. |
| Discovery of a new area | Overview for topology discovery, strict for local discovery. | Send once per discovered area/map revision. |
| Combat rounds | Strict grid. | Send every 5 rounds by default, keyed by combat id/map id/round bucket. |
| Ordinary spatial changes | None by default. | Do not auto-send for v1 unless another trigger applies. |

Implementation should use explicit cadence state, for example a scene-level
internal record keyed by render family, map id, map revision/layout revision,
trigger kind, and combat round bucket. Missing state in old sessions means no
previous v1 sends. This state is internal delivery metadata and must be blocked
from ordinary prompt/player projections.

The state must answer these questions before enqueueing a map:

- Is this trigger eligible for a player-facing render?
- Which deterministic renderer family should be attempted first?
- Has this map/revision/trigger already been sent?
- Is legacy fallback allowed for this trigger?
- Should failure be silent, logged, or summarized to the player?

## Player View / Hidden Fact Leakage Guard

Map delivery must consume renderer outputs derived from `player_view`. It must
not inspect raw MapStore state, hidden topology, raw strict grids, raw SVG, local
paths, or URLs to decide player-facing content.

Required guards:

- Call deterministic renderer tools only after the corresponding player-view map
  family is renderable.
- Keep hidden facts out of renderer envelopes.
- Keep SVG/PNG artifacts out of backend input and map facts.
- Keep local artifact paths inside delivery internals and logs only.
- Keep cadence state out of prompts and projected scene facts.
- Treat legacy `last_map_svg` only as a safe render-reference display record.
- Treat layout updates as render-layout metadata, not topology or map authority.

If deterministic rendering fails because player-view data is missing, the system
may either return a stable non-rendered response or use legacy fallback only when
the fallback policy explicitly allows it. It must not ask an LLM to reconstruct
hidden topology from narration or old SVG.

## Pending Output / Chat Delivery Plan

V1 should reuse the existing `_pending_outputs` chat attachment channel:

- Keep `type: "svg_map"` so current attachment code recognizes map artifacts.
- Require `visual_only: true` for map delivery records.
- Prefer `render_type: "strict_grid_svg"` or
  `render_type: "overview_topology_svg"` for deterministic outputs.
- Allow legacy/no-`render_type` records only as compatibility fallback.
- Keep `path` as internal delivery metadata and strip it from prompt/player
  projections.
- Keep pending output length bounded.
- Pop pending outputs after the DM result path consumes them.

Delivery cadence should decide whether to call a renderer; renderer tools should
continue to decide how to enqueue their own pending output once called. If later
implementation needs auto-triggered sends without LLM tool calls, add a narrow
delivery service that calls renderer functions/tools through code-owned
contracts instead of reimplementing renderer logic in `main.py`.

## SVG/PNG Preview Reuse Plan

Reuse current SVG-to-PNG preview infrastructure in `main.py` for v1 because it
already handles `svg_map` attachments and both deterministic renderers emit
simple SVG subsets.

Required hardening:

- If PNG conversion fails, do not print local file paths to player chat.
- Log internal file paths for debugging instead of sending them as player text.
- Keep attachment limits conservative, matching current `pending_outputs[:2]`.
- Keep preview conversion best-effort; perfect rendering across every chat
  client remains out of scope.
- Do not introduce a new rendering dependency unless current preview behavior
  proves insufficient for deterministic strict/overview outputs.

## Audit / Old Session Compatibility Plan

Audit:

- Keep tool audit for `generate_map_svg`, `render_strict_grid_svg`, and
  `render_overview_topology_svg`.
- Add delivery/cadence audit events only if implementation changes automatic
  send decisions.
- Audit records may include internal metadata, but ordinary DM/player prompt
  projection must not consume them as facts.
- Audit should record fallback decisions, skipped sends, duplicate suppression,
  and renderer missing-data outcomes when useful for debugging.

Old sessions:

- Do not migrate old session files in place.
- Missing cadence state means no prior v1 delivery state.
- Existing `scene["last_map_svg"]` remains a safe legacy visual reference after
  projection.
- Existing `_pending_outputs` may be popped through the existing path if safe.
- Old pending `svg_map` records without `render_type` are treated as legacy
  delivery-only records.
- Legacy `battle.grid` remains a migration source through already landed strict
  renderer compatibility, not a delivery contract.

## This Task Adds

Implementation after this PRD should add:

- a code-owned delivery cadence policy and state contract;
- deterministic-first map route selection for normal map requests;
- registry exposure changes that hide legacy `generate_map_svg` from normal
  deterministic-capable routes;
- prompt guidance changes that describe deterministic renderers as the normal
  path and legacy SVG as fallback only;
- chat delivery hardening so local artifact paths are not sent to players;
- focused compatibility behavior for old `last_map_svg`, old pending outputs,
  and no-cadence-state sessions;
- tests for cadence triggers, duplicate suppression, deterministic routing,
  fallback visibility, projection safety, and delivery output safety.

## Conflicts / Tensions

- The current registry still appends `generate_map_svg`, so legacy SVG remains
  normal unless routing changes.
- Current prompt hints still prefer `generate_map_svg` for ordinary visual map
  requests.
- Current chat fallback may print local file paths when PNG preview fails.
- `main.py`, `map_tools.py`, `registry.py`, and `core/prompts.py` are already
  large. Cadence logic should be introduced through a cohesive module and narrow
  integration hooks, not by adding large new blocks to those files.
- The LLM can still choose tools in normal flow. Code must own availability,
  fallback permissions, cadence state, and delivery side effects so prompt text
  is not the only enforcement layer.
- Old sessions may contain legacy visual fields. Compatibility must not turn
  those fields into renderer input or fact authority.
- Automatic sends can spam chat if cadence state is underspecified. Duplicate
  suppression is part of v1, not a later polish item.

## Out of Scope

- Defining the coordinate contract.
- Implementing strict grid renderer geometry.
- Implementing overview topology layout.
- Perfect preview rendering across all chat clients.
- Removing legacy code before deterministic replacements are stable.
- Rewriting MapCore facts from SVG, PNG, render refs, or delivery state.
- Full legacy cleanup of `battle.grid`, `last_map_svg`, and old SVG fields.
- Per-character/private vision rendering.
- Advanced facing/FOV-aware map delivery.
- Broad UI or manual map editor work.

## Purpose And Means Alignment

Purpose:

- Make map delivery predictable and player-safe.
- Make deterministic renderers the normal path now that both renderer families
  exist.
- Keep legacy SVG only as compatibility, fallback, or style experimentation.
- Prevent SVG/PNG artifacts and delivery metadata from becoming map truth.
- Avoid spam by making cadence state explicit.

Means:

- Code owns renderer availability, route selection, fallback permission,
  cadence state, duplicate suppression, delivery metadata, and projection
  safety.
- Deterministic renderer tools produce visual-only artifacts from `player_view`.
- Existing `_pending_outputs` and SVG-to-PNG preview are reused for v1.
- Prompt and registry exposure are updated so normal map requests prefer
  deterministic renderers.
- Legacy `generate_map_svg` stays available only through explicit fallback or
  migration-only paths.

Trade-offs:

- V1 may send fewer maps than a purely prompt-driven system because cadence is
  conservative.
- Some artistic legacy map sketches remain possible only through fallback.
- A small delivery policy module adds state complexity, but it prevents spam and
  keeps routing out of renderer geometry.
- Local-path chat hardening may reduce manual fallback visibility when preview
  conversion fails, but it keeps internal paths out of player chat.

## Agent-Code Responsibility Split

Code owns:

- current map family detection and renderability checks;
- `player_view` projection selection;
- deterministic renderer selection;
- legacy fallback permission;
- cadence trigger evaluation;
- duplicate suppression state;
- pending output delivery metadata;
- local path privacy;
- audit events;
- tests and compatibility behavior;
- no-SVG-to-fact enforcement.

LLM / agent may help with:

- understanding a player's natural-language request for "map", "route",
  "battlefield", "where are we", or similar intent;
- choosing a tool from code-exposed deterministic options;
- producing fallback visual style instructions only when legacy fallback is
  explicitly allowed;
- explaining to the player that a map is unavailable because structured
  player-view data is missing.

LLM / agent must not:

- write normal final SVG/XML;
- decide hidden fact visibility;
- read raw hidden map facts or raw SVG as map context;
- decide that SVG/PNG output should rewrite map facts;
- bypass cadence state;
- decide local artifact paths are safe to show to players.

## Atomic Commit Plan

| Commit | Purpose | Includes | Excludes | Depends on | Validation |
| --- | --- | --- | --- | --- | --- |
| 1 | Document delivery cadence and legacy SVG migration PRD. | This PRD under `docs/`. | Runtime behavior. | Current `main` with PR #19 and PR #20. | `git diff --check`; privacy scan for local paths. |
| 2 | Add delivery cadence policy and state contract. | Cohesive module for map delivery decisions, state keys, duplicate suppression, and unit tests. | Registry/prompt migration; chat send hooks; renderer geometry. | Commit 1. | New cadence unit tests; compileall. |
| 3 | Route normal map requests deterministic-first. | Tool exposure/routing policy, prompt hint updates, tests for overview/strict/legacy fallback availability. | Chat attachment changes; renderer internals. | Commit 2. | `tests/test_tool_registry.py`, `tests/test_prompts.py`, targeted routing tests. |
| 4 | Integrate cadence with pending output delivery. | Narrow hooks in normal DM result path, renderer call orchestration if needed, no-spam behavior, skipped-send audit. | Legacy code deletion; broad direct-send rewrites. | Commits 2-3. | Cadence integration tests and existing pending output tests. |
| 5 | Harden SVG/PNG preview fallback and compatibility. | No local path in player chat on preview failure, old pending output compatibility, `last_map_svg` compatibility tests. | New preview subsystem. | Commit 4. | Delivery tests, projection tests, `git diff --check`. |
| 6 | Sync docs and cleanup migration notes. | Update renderer/delivery docs to match final behavior and residual legacy policy. | Removing legacy `generate_map_svg`. | Commits 2-5. | Docs review, targeted full command list below. |

Commit boundaries may be adjusted after line-count and code-shape inspection, but
each commit should remain independently reviewable, testable, and revertable.

## Work Rounds / Commit Checkpoints

| Round | Complete when | Validate with | Commit immediately? | Next round starts after |
| --- | --- | --- | --- | --- |
| 1 | PRD is written and reviewed. | `git diff --check`; no local workflow paths in docs. | Yes, if the PRD is approved for commit. | Clean or PRD-only status. |
| 2 | Cadence policy can decide send/skip for trigger fixtures without touching chat. | New unit tests for trigger eligibility and duplicate suppression. | Yes. | Commit 2 hash recorded. |
| 3 | Tool/prompt exposure prefers deterministic renderers and hides legacy fallback by default. | Registry and prompt tests. | Yes. | Commit 3 hash recorded. |
| 4 | Normal DM flow can enqueue/send maps according to cadence state. | Integration tests around pending outputs and duplicate suppression. | Yes. | Commit 4 hash recorded. |
| 5 | Preview failure and old-session compatibility are safe. | Delivery/projection compatibility tests. | Yes. | Commit 5 hash recorded. |
| 6 | Public docs match implementation. | `git diff --check`; targeted pytest list. | Yes. | Ready for PR review. |

Before editing any code file, check line count. If a file is near 1200 lines,
plan a responsibility-based split before adding substantial logic. If a file is
near or above 1500 lines, split first or document a short-term exception. Do not
split only by generic interfaces, constants, or utilities.

## Acceptance Criteria

- Both deterministic renderer families are treated as landed prerequisites.
- Player-facing map sends use render outputs derived from `player_view`.
- Normal overview map requests route to `render_overview_topology_svg` when
  player-view overview topology exists.
- Normal strict/tactical map requests route to `render_strict_grid_svg` when a
  player-view strict map exists.
- Legacy `generate_map_svg` is not exposed as the normal path when a
  deterministic renderer is available.
- Legacy `generate_map_svg` remains available only as explicit temporary
  fallback / style experiment / migration-only path.
- SVG/PNG delivery never becomes backend input, fact source, coordinate source,
  topology source, or reason to rewrite map facts.
- Legacy SVG output remains `visual_only`.
- Delivery cadence follows the v1 trigger contract and does not send on every
  ordinary spatial change.
- Duplicate suppression prevents repeated sends for the same map/revision/trigger
  or combat round bucket.
- Chat delivery remains compatible with existing `svg_map` pending records.
- Chat fallback does not expose local artifact paths to players.
- Old sessions without cadence state remain readable.
- Old `last_map_svg` records remain safe visual references and are not renderer
  inputs.
- Prompt projection continues to block raw SVG, local paths, URLs, raw grids,
  layout updates, and hidden facts.
- Audit and tests demonstrate that no prompt, tool schema, chat delivery path, or
  old session consumer relies on legacy SVG as a fact source.

## Verification Plan

Docs-only PRD verification:

```bash
git diff --check
git diff -- docs/delivery-cadence-legacy-svg-migration-prd.md
```

Implementation verification should include targeted tests such as:

```bash
python -m pytest -q tests/test_tool_registry.py tests/test_prompts.py tests/test_prompt_projection.py tests/test_strict_grid_render_tools.py tests/test_overview_topology_render_tools.py -p no:cacheprovider
python -m pytest -q tests/test_dm_ack_and_outputs.py tests/test_long_running_reassurance.py -p no:cacheprovider
python -m compileall -q astrbot_plugin_auto_trpg_dm tests
git diff --check
```

Add or rename targeted tests to match the final implementation modules. At a
minimum, new tests should cover:

- trigger eligibility and skip reasons;
- duplicate suppression state;
- player request override;
- overview transition send;
- strict exploration/combat start and end send;
- new-area discovery send;
- combat every-5-round behavior;
- no send for ordinary spatial changes alone;
- deterministic-first registry/tool exposure;
- legacy fallback hidden from normal tools;
- no local path in chat fallback;
- old `last_map_svg` compatibility;
- old pending `svg_map` compatibility;
- no SVG/PNG-to-map-fact writeback;
- prompt projection blocking cadence state, local paths, raw SVG, layout updates,
  URLs, and hidden facts.

# Overview Topology SVG Renderer PRD

## Current / Already Done

- `docs/projection-consumer-matrix.md` defines the renderer handoff boundary:
  renderer input is `player_view` plus an explicit render request/envelope, and
  renderer output is render refs, artifact metadata, and delivery metadata.
- `docs/coordinate-renderer-contract.md` defines the coordinate and layout
  contract for future deterministic map renderers. It explicitly keeps rendered
  SVG/PNG artifacts out of authoritative map facts.
- `astrbot_plugin_auto_trpg_dm/core/map_core.py` already has:
  - `MAP_VIEW_PLAYER`, `MAP_VIEW_DM_NARRATION`, `MAP_VIEW_RA_AUTHORITY`, and
    `MAP_VIEW_DIAGNOSTIC`;
  - map visibility constants for `public`, `player`, `dm`, `hidden`, and
    `diagnostic`;
  - overview map type constants;
  - `project_map_store()` and `project_active_map_record()`;
  - `add_render_ref()` for visual-only render references.
- The legacy visual map path still exists in
  `astrbot_plugin_auto_trpg_dm/tools/map_tools.py` as `generate_map_svg()`.
  That path calls an LLM sub-context to generate SVG, writes
  `scene["last_map_svg"]`, and appends `type: "svg_map"` records to
  `scene["_pending_outputs"]`.
- Chat delivery in `astrbot_plugin_auto_trpg_dm/main.py` already consumes
  `_pending_outputs` and attaches only records with `type == "svg_map"`.
- Prompt guidance in `astrbot_plugin_auto_trpg_dm/core/prompts.py` now prefers
  `render_overview_topology_svg` for non-combat overview requests when a
  structured overview topology exists, while keeping `generate_map_svg` as a
  legacy compatibility path.
- `render_overview_topology_svg` now renders deterministic overview topology
  SVG from player-projected topology facts and records visual refs / pending
  delivery metadata without asking an LLM to write SVG/XML.
- Generated layout positions are cached under
  `record["archive_identity"]["overview_topology_layout"]`; renderer output
  does not create or modify topology map facts.
- PR #18 has landed on `main` and brought in the coordinate renderer contract.
- PR #19 is the sibling strict-grid SVG renderer work. It is not part of this
  PRD's baseline and should be treated as a parallel renderer path.

## Prerequisite Status

This task can start from current `main` because the coordinate renderer contract
is already present there through PR #18.

The strict-grid renderer from PR #19 is not a prerequisite for this overview
renderer. The overview renderer should not stack on strict-grid implementation
commits unless a future merge conflict requires a small compatibility follow-up.

Required prerequisite contracts:

- Renderer input must be `player_view` plus an explicit render request/envelope.
- Renderer output must be render refs, artifact metadata, and delivery metadata.
- Rendered SVG is visual output only and must not update map facts.
- Hidden coordinates, hidden layout records, raw MapStore state, raw strict
  grids, local artifact paths, URLs, and raw SVG must not enter ordinary player
  or DM prompt context.

## Relationship To Strict Grid Renderer

The strict-grid renderer and overview topology renderer are sibling renderers.
They share the renderer handoff contract but render different map families.

| Area | Strict grid renderer | Overview topology renderer |
| --- | --- | --- |
| Map family | Tactical or strict local maps. | Non-combat overview maps. |
| Primary input | Grid-compatible visible cell data. | Player-visible topology: nodes, edges, areas, landmarks, current marker, and layout anchors. |
| Player precision | Grid geometry and rule scale can be visible. | Numeric coordinates are hidden by default; relationships and rough distance are preferred. |
| Hidden data risk | Grid extent, hidden cells, entities, raw battle grid. | Blank space, hidden routes, hidden nodes, true backend layout anchors. |
| Output type | `svg_map` with renderer-specific metadata. | `svg_map` with `render_type: "overview_topology_svg"`. |

The two renderer paths may share generic SVG serialization helpers only if those
helpers stay renderer-neutral. They should not share grid-specific layout,
overview-specific topology placement, or hidden-leakage rules.

## Impact Scan Evidence

### Scene Summaries

`scene["summary"]` appears across memory, prompts, routing, main plugin flow, and
tools. It is narrative context, not structured topology authority.

Evidence:

- `core/memory.py` uses `scene["summary"]` as compact scene text.
- `core/router.py` repairs or initializes scene summary patches.
- `tools/map_tools.py` uses the summary as a fallback prompt for legacy
  `generate_map_svg()`.
- `tools/ambient_image_tools.py` uses scene summaries for ambient image prompts
  and rhythm decisions.

Decision: overview SVG rendering must not parse scene summary into authoritative
topology during the normal render path. If future agent work extracts topology
from narration, that output must be a candidate event that code validates before
it becomes map state.

### Map Facts

`core/map_core.py` currently stores map records with `facts` and `render_refs`.
Projected facts preserve `payload` when it survives the visibility projection.
There is no dedicated overview topology envelope yet.

Evidence:

- `default_map_store()` initializes active overview and strict map slots.
- `_project_map_record()` filters records and facts by allowed visibility.
- `_project_fact()` projects fact payloads.
- `_project_render_ref()` removes raw path and URL fields from prompt-facing
  render refs.

Decision: this task should introduce or document an overview topology fact shape
for player-visible nodes, edges, areas, landmarks, and layout anchors before the
renderer consumes it.

### Visibility Projection

`player_view` allows only `public` and `player` facts. DM narration and RA
authority can include `dm` facts through their own views.

Evidence:

- `_allowed_visibility_for_view(MAP_VIEW_PLAYER)` returns only public/player
  visibility.
- `docs/projection-consumer-matrix.md` forbids raw MapStore authority state and
  hidden facts for renderer/player map output.

Decision: overview renderer input must be built from `MAP_VIEW_PLAYER`, not raw
`session.maps`, raw scene state, or RA authority snapshots.

### Renderer Inputs

The production baseline has no deterministic `player_view` renderer consumer.
The legacy renderer reads compact battle state and asks an LLM to draw SVG.

Evidence:

- `docs/projection-consumer-matrix.md` says no production deterministic
  `player_view` renderer consumer exists yet.
- `tools/map_tools.py` reads `session.compact_snapshot().get("battle", {})`
  inside `generate_map_svg()`.

Decision: overview rendering needs its own request/envelope and adapter from
projected map records to renderer input. It should not reuse the legacy
`GenerateMapSvgArgs` prompt contract.

### `last_map_svg`

`scene["last_map_svg"]` is a legacy visual reference. It can be projected as a
safe title/name render reference, but it is not source of truth.

Evidence:

- `tools/map_tools.py` writes `latest_session.scene["last_map_svg"]`.
- `core/prompts.py` drops raw `last_map_svg` from normal scene projection, then
  re-adds a projected safe map ref.
- `docs/coordinate-renderer-contract.md` classifies `last_map_svg` as legacy
  compatibility.

Decision: overview renderer may leave `last_map_svg` compatible if needed, but
it should write first-class render refs and pending output metadata. It must not
read `last_map_svg` to reconstruct topology or layout.

### Chat Delivery

Chat delivery currently accepts pending records with `type == "svg_map"` and
uses internal paths to render PNG previews for chat attachment.

Evidence:

- `_quoted_result()` iterates over `_pending_outputs[:2]` and skips entries
  whose type is not `svg_map`.
- `_ensure_png_preview()` reads internal SVG paths for attachment conversion.
- `_pop_pending_outputs()` clears pending records after popping them.

Decision: overview renderer output should reuse `type: "svg_map"` for delivery
compatibility and add `render_type: "overview_topology_svg"` to distinguish the
renderer family. Internal paths stay delivery-only metadata and must not enter
prompt/player facts.

### Tool Schemas

`generate_map_svg` is currently the only visual SVG map tool on `main`. Its
schema is prompt-oriented and LLM-driven.

Evidence:

- `tools/map_tools.py` defines `GenerateMapSvgArgs`.
- `tools/registry.py` registers `generate_map_svg`.
- `_with_llm_decided_tools()` adds `generate_map_svg` unless the message looks
  text-only.

Decision: overview rendering should use a separate deterministic tool schema,
for example `render_overview_topology_svg`. It should accept bounded display and
delivery options, not a free-form drawing prompt as the primary contract.

### Prompt Guidance

The prompt currently says visual map requests should call `generate_map_svg`,
and local visual intent hints reinforce that path.

Evidence:

- `core/prompts.py` rule 26 tells the DM to call `generate_map_svg` for visual
  maps and notes that the tool uses an isolated LLM sub-context.
- `_looks_like_visual_map_request()` guidance says to prefer `generate_map_svg`
  when allowed.

Decision: this task should conservatively add overview-specific guidance only
after the deterministic tool exists. It should not remove legacy guidance in a
way that breaks strict/tactical legacy map requests; broad migration belongs to
a delivery/legacy cleanup PR.

### RA Outputs

RA authority snapshots include projected map data through `MAP_VIEW_RA_AUTHORITY`
and sanitized payloads. RA output is not player-facing renderer input.

Evidence:

- `build_ra_authority_snapshot()` uses `project_map_store(...,
  MAP_VIEW_RA_AUTHORITY)`.
- `RecorderAgent.run_cycle_resolution()` calls the LLM and validates structured
  RA summaries.
- `docs/projection-consumer-matrix.md` separates RA authority from renderer
  player output.

Decision: RA may produce topology maintenance candidates in future work, but the
overview renderer must consume only code-projected `player_view` data.

### Tests That Assume SVG Generation Is LLM-Driven

Existing tests cover legacy prompt projection and map SVG tool presence. They
should not be rewritten wholesale in this task.

Evidence:

- `tests/test_prompt_projection.py` uses a `generate_map_svg` tool result and
  checks hidden/path/raw data filtering.
- `tests/test_tool_registry.py` covers `generate_map_svg` exposure through the
  registry.
- `tests/test_prompts.py` covers `last_map_svg` projection behavior.

Decision: overview tests should be additive. They should prove deterministic
overview rendering, leakage guards, delivery metadata, and registry exposure
without deleting legacy LLM SVG expectations.

### Player-View Topology Consumers

No production overview topology renderer consumes `player_view` yet. Existing
projection tests use overview map records as fixtures but do not render a map.

Evidence:

- `tests/test_map_core.py` creates overview map records and verifies hidden fact
  filtering and safe render ref projection.
- `tests/test_prompts.py` verifies hidden map facts and local paths do not enter
  projected prompt snapshots.

Decision: this task adds the first production player-view overview topology
consumer. It must be strict about input projection and should include tests that
fail if hidden topology affects player-visible SVG bounds or layout.

## Overview Topology Renderer Contract

The overview topology renderer deterministically renders non-combat overview
maps from structured, player-visible topology and layout data.

The renderer must:

- consume only an explicit overview render envelope built from `player_view`;
- render discovered places, routes, rough direction, area grouping, landmarks,
  and current location;
- avoid false precision by default;
- preserve established node positions across repeated renders;
- add newly discovered nodes without reshuffling old known topology;
- avoid blank-space, bounds, layout, or style leaks for hidden topology;
- generate SVG/XML through code, not an LLM;
- write visual artifacts only as render refs, artifact metadata, and delivery
  metadata;
- never write SVG output back into authoritative map facts.

## Input Contract

The recommended v1 input is an `OverviewTopologyRenderEnvelope` with these
fields:

| Field | Required | Notes |
| --- | --- | --- |
| `render_type` | Yes | Must be `overview_topology_svg`. |
| `map_id` | Yes | Player-visible overview map id. |
| `map_revision` | Yes | Source map fact revision or stable equivalent. |
| `layout_revision` | No | Existing layout revision if present. |
| `title` | No | Safe display title from player projection. |
| `nodes` | Yes | Player-visible places or points of interest. |
| `edges` | Yes | Player-visible or suspected routes between visible endpoints. |
| `areas` | No | Player-visible groupings, regions, districts, floors, or zones. |
| `landmarks` | No | Player-visible landmark markers or labels. |
| `current_node_id` | No | Current player-facing location marker. |
| `layout` | No | Stored player-visible layout positions and anchors. |
| `display_profile` | No | Width, height, theme, label density, coordinate visibility. |
| `delivery` | No | Whether to enqueue chat delivery. |

Node fields:

- `id`
- `label`
- `kind`
- `visibility`
- `status`
- `area_id`
- `layout_pos`, when visible and already stored
- `anchor`, when a stable non-coordinate anchor exists
- `relationship_notes`, when safe

Edge fields:

- `id`
- `source_id`
- `target_id`
- `relationship`
- `direction`
- `distance_band`
- `route_group`
- `status`, such as `known`, `suspected`, or `known_but_unseen`
- `landmark_ids`

Area fields:

- `id`
- `label`
- `kind`
- `visibility`
- `node_ids`
- `style_hint`, if safe and bounded

Landmark fields:

- `id`
- `label`
- `node_id` or `edge_id`
- `kind`
- `visibility`

Input validation should reject:

- raw MapStore input;
- raw `battle.grid`;
- `scene["last_map_svg"]` as topology input;
- local file paths or URLs in player-facing input;
- hidden records;
- edges whose endpoints are hidden or absent;
- exact numeric coordinate display unless the envelope explicitly allows it.

## Output Contract

The renderer should return an `OverviewTopologyRenderResult` with:

| Field | Notes |
| --- | --- |
| `ok` | Boolean success. |
| `render_type` | `overview_topology_svg`. |
| `map_id` | Source map id. |
| `map_revision` | Source map revision. |
| `layout_revision` | Existing or newly created layout revision. |
| `file_path` / `file_name` | Internal artifact result. Local paths should not enter prompt/player projection. |
| `width` / `height` | Display dimensions, not physical scale. |
| `render_ref` | Safe render ref payload; internal path/url omitted when projected. |
| `pending_output` | Optional `type: "svg_map"` delivery record. |
| `layout_updates` | Non-coordinate summary of newly cached layout updates; ordinary DM prompt projection blocks this key. |
| `warnings` | Non-fatal validation or fallback notes. |

Pending delivery records should use:

```json
{
  "type": "svg_map",
  "render_type": "overview_topology_svg",
  "title": "Overview Map",
  "name": "overview-map.svg",
  "path": "<internal delivery path>",
  "width": 900,
  "height": 700,
  "visual_only": true
}
```

The `path` field is internal delivery metadata only. Prompt/player projection
must continue to expose only safe render ref fields. Raw SVG is written to the
artifact file, not returned as prompt-safe content.

## Hidden Topology Leakage Guard

The renderer must not leak hidden topology through:

- SVG bounds;
- canvas size;
- empty reserved regions;
- stable positions for hidden future nodes;
- hidden route curvature;
- hidden edge labels;
- exact hidden coordinates;
- diagnostic metadata;
- local artifact paths;
- raw SVG content in prompts.

Rules:

- Compute player-facing bounds from player-visible render elements only.
- Drop hidden nodes and hidden edges before layout.
- Drop edges if either endpoint is not present in the projected node set.
- Render `suspected` and `known_but_unseen` relationships only when the
  relationship itself is player-visible.
- Use dashed or muted styling for suspected relationships, but do not use their
  backend coordinates.
- Do not leave gaps that imply undiscovered room count, route length, or map
  extent.
- Keep layout revisions separate from map fact revisions.

## Layout Persistence Plan

The renderer should prefer stored player-visible layout positions. If positions
are missing, it should compute a deterministic topology layout and persist the
result as a render-layout update, not as map fact data.

Default deterministic layout:

1. Select the current node when present; otherwise select a stable root node.
2. Build graph layers using BFS over player-visible nodes and edges.
3. Stable-sort siblings by stored order, then label, then id.
4. Place layers into fixed slots with deterministic spacing.
5. Place newly discovered nodes near their discovered parent or edge anchor.
6. Preserve existing node positions unless a visible topology change makes them
   invalid.
7. Fit visible bounds into the display profile with padding.

Persistence rules:

- `map_revision` tracks authoritative topology/fact changes.
- `layout_revision` tracks render placement, label density, style, masks, and
  display profile choices.
- Generated layout positions are cached in
  `record["archive_identity"]["overview_topology_layout"]["positions"]`.
- Cached positions are merged back into the next render envelope only for node
  ids already present in the player-projected topology payload.
- `layout_updates` in the raw tool result reports non-coordinate update
  metadata such as `cached`, `layout_revision`, and `generated_node_ids`.
- `layout_updates`, `layout`, and `positions` remain blocked from ordinary DM
  prompt projection.
- Layout updates must not create or modify map facts.
- Returning to an archived scene may reuse layout only when map identity and
  relevant revisions still match.

## Compatibility Routing Matrix

| Request / State | Route | Notes |
| --- | --- | --- |
| Player asks for a non-combat overview map and a player-visible overview topology exists. | `render_overview_topology_svg` | Preferred deterministic path. |
| Player asks for tactical grid / strict local map. | Strict-grid renderer if available; otherwise existing strict/tactical path. | This PRD does not implement strict grid. |
| Player asks for visual map but no structured overview topology exists. | Return a stable `overview_topology_missing` error or use legacy fallback only if explicitly allowed by later routing work. | Do not make LLM SVG authoritative. |
| Legacy prompt calls `generate_map_svg`. | Keep compatibility. | Do not remove in this task. |
| Pending output has `type: "svg_map"` and `render_type: "overview_topology_svg"`. | Existing chat delivery can attach it. | Delivery code may need only minimal metadata handling. |
| Pending output has `type: "svg_map"` and legacy/no render type. | Existing delivery behavior. | Keep for compatibility. |
| Hidden-only route or hidden-only node exists in backend. | Omit from overview renderer input. | No blank-space leakage. |

## Delivery / Pending Output Plan

V1 reuses `_pending_outputs` and `type: "svg_map"` for chat attachment
compatibility. The overview renderer adds `render_type:
"overview_topology_svg"`, `width`, `height`, `visual_only`, and
renderer-specific metadata.

This task should not redesign delivery cadence. Major delivery policy,
legacy-downgrade policy, and broad migration from `generate_map_svg` belong to a
later delivery/legacy migration PR.

The overview render tool supports a `send_to_chat` option. When true, it appends
a pending output record with an internal SVG path. When false, it still writes
the SVG artifact and records a visual-only render ref if rendering succeeded.

## This Task Adds

- A deterministic overview topology SVG renderer.
- A player-view overview render envelope builder / adapter.
- A bounded `render_overview_topology_svg` tool schema.
- Registry exposure that does not remove legacy `generate_map_svg`.
- Layout persistence in record archive metadata plus layout-update return
  semantics.
- Additive tests for:
  - deterministic layout stability;
  - new node placement without old-node reshuffle;
  - hidden topology leakage guards;
  - default coordinate hiding;
  - render output not writing map facts;
  - pending output metadata;
  - prompt/tool projection safety.
- Public docs/spec updates that explain renderer behavior and compatibility.

## Conflicts / Tensions

- PR #19 may touch renderer routing, tool registry, pending SVG delivery, prompt
  guidance, and tests. This task should minimize shared-file edits and resolve
  conflicts after PR #19 lands if needed.
- Legacy `generate_map_svg` is still prompt-preferred. Replacing all visual-map
  guidance at once would mix overview renderer work with delivery migration.
- Current map facts do not yet have a dedicated overview topology schema. The
  implementation may need a minimal shape that is strict enough to test without
  forcing a full map fact migration.
- This PR uses the existing overview topology fact payload shape as the
  structured input. A future stable map fact model can formalize that payload
  further without making renderer output authoritative.
- `main.py` only checks `type == "svg_map"` and has no renderer-family routing
  today. Overview should add metadata conservatively rather than redesigning
  delivery.
- `project_map_store()` currently projects fact payloads. The renderer adapter
  must still validate the payload shape before drawing.
- `scene["summary"]` is tempting as fallback topology source, but using it would
  reintroduce LLM/prose authority into renderer behavior.

## Out of Scope

- Strict grid rendering.
- True GIS or geographic projection.
- Perfect aesthetic map generation.
- Full route-cost visualization in v1.
- Exposing DM fact map hidden topology.
- Broad deletion or retirement of `generate_map_svg`.
- Full delivery cadence migration.
- Making SVG/PNG artifacts source of truth.
- Inferring authoritative topology directly from scene summaries.
- Rendering raw battle/grid snapshots.

## Purpose And Means Alignment

Purpose:

- Give players a stable overview map of discovered topology: places, routes,
  rough direction, area relationships, landmarks, and current location.
- Avoid both false precision and hidden topology leakage.
- Reduce cost and fragility by replacing normal overview SVG generation with
  deterministic code.

Means:

- Use structured player-view topology/layout data.
- Build SVG/XML through deterministic Python code.
- Persist layout separately from map facts.
- Keep legacy LLM SVG as compatibility only.

Trade-offs:

- V1 will be less artistic than LLM-generated SVG.
- V1 needs stricter input shape and tests before it can render rich maps.
- Some visual-map requests may still fall back to legacy behavior until the
  delivery/legacy migration PR.
- Layout persistence adds state responsibility but prevents repeated renders
  from reshuffling the player-visible map.

## Agent-Code Responsibility Split

Code owns:

- projection choice;
- renderer envelope building;
- topology payload validation;
- hidden leakage guards;
- deterministic layout;
- SVG serialization;
- artifact writing;
- render ref and pending output creation;
- layout persistence;
- tests and fallback behavior.

Agents or LLMs may own only:

- converting natural-language map descriptions into structured candidate events;
- summarizing narrative context for a human or DM;
- proposing labels or descriptions before code validation.

Agents or LLMs must not:

- write the normal final SVG/XML;
- decide whether hidden topology is safe to reveal;
- write authoritative map facts directly;
- infer layout from raw SVG;
- update map fact revisions from renderer output;
- decide delivery side effects.

## Atomic Commit Plan

| Commit | Purpose | Includes | Excludes | Depends on | Validation |
| --- | --- | --- | --- | --- | --- |
| 1 | Document overview topology renderer PRD. | `docs/overview-topology-svg-renderer-prd.md`. | Runtime behavior. | Current `main`. | Markdown review, `git diff --check`. |
| 2 | Add overview topology input and layout contracts. | Renderer input dataclasses/helpers or focused module; tests for validation and hidden endpoint rejection. | SVG drawing, tool registry, chat delivery. | Commit 1. | Targeted unit tests for input validation. |
| 3 | Add deterministic layout and SVG renderer core. | Overview layout algorithm, SVG serialization, label/edge/area/current-marker layers. | Tool exposure, pending outputs. | Commit 2. | Renderer snapshot/structural tests; hidden-bounds tests. |
| 4 | Add render tool and registry integration. | Tool args, handler, repository integration, render refs, optional pending output. | Broad prompt migration or delivery cadence redesign. | Commit 3. | Tool registry tests and render tool tests. |
| 5 | Add projection and compatibility tests. | Prompt projection, `last_map_svg` compatibility, pending output metadata, no map-fact writeback tests. | Removing legacy LLM SVG path. | Commit 4. | Targeted pytest for prompt, map core, registry, delivery behavior. |
| 6 | Conservatively update prompt/docs guidance. | Prompt guidance that prefers deterministic overview when structured overview exists; docs update. | Strict-grid guidance, full legacy cleanup. | Commits 4-5. | Prompt tests, docs review, `git diff --check`. |

## Work Rounds / Commit Checkpoints

| Round | Complete when | Validate with | Commit immediately? | Next round starts after |
| --- | --- | --- | --- | --- |
| 1 | PRD committed. | `git diff --check`. | Yes. | Clean status after docs commit. |
| 2 | Input contract rejects unsafe shapes. | Targeted renderer input tests. | Yes. | Commit 2 hash recorded locally. |
| 3 | Renderer core can draw stable SVG from fixtures. | Renderer unit tests and hidden-bounds tests. | Yes. | Commit 3 hash recorded locally. |
| 4 | Tool path can render and enqueue safely. | Tool and registry tests. | Yes. | Commit 4 hash recorded locally. |
| 5 | Compatibility/projection regressions covered. | Prompt, map core, delivery tests. | Yes. | Commit 5 hash recorded locally. |
| 6 | Guidance/docs match implementation. | Prompt tests and `git diff --check`. | Yes. | Ready for PR review. |

Before editing any code file in implementation rounds, check the file length.
If a target file is near 1200 lines, plan a responsibility-based split first.
If it is near or above 1500 lines, split it or document the short-term exception
before editing.

## Acceptance Criteria

- Rendering uses structured player-view topology/layout data.
- The normal overview render path does not call an LLM to write SVG/XML.
- Repeated renders preserve established player-visible node positions.
- Newly discovered nodes are added without reshuffling old known topology.
- Edges render before areas, then landmarks, nodes, labels, and current marker.
- Numeric coordinates are hidden by default.
- Rendered labels emphasize relationship, direction, rough distance, route
  grouping, and landmarks rather than false physical precision.
- Suspected or known-but-unseen relationships use dashed or muted styling only
  when the relationship itself is player-visible.
- Hidden nodes and routes do not affect player-visible canvas bounds, blank
  space, or layout positions.
- SVG output is visual-only and does not write map facts.
- Pending output remains compatible with existing `svg_map` chat delivery.
- Legacy `generate_map_svg` tests remain valid unless a later migration PR
  explicitly changes that contract.

## Verification Plan

Targeted tests:

- overview input validation rejects raw MapStore, raw battle grid, hidden nodes,
  hidden edges, missing endpoints, raw paths, and URLs;
- deterministic layout preserves existing positions;
- deterministic layout places newly discovered nodes near stable parent or edge
  anchors;
- hidden topology does not change bounds or reserve obvious blank space;
- SVG structure includes expected layer order and current-location marker;
- render result produces visual-only render ref metadata;
- render result includes `map_revision`, `layout_revision`, `width`, `height`,
  and non-coordinate `layout_updates`;
- generated layout positions are persisted under map record archive metadata,
  not topology map facts;
- `send_to_chat=True` appends `type: "svg_map"` and
  `render_type: "overview_topology_svg"`;
- projected prompts do not expose raw SVG, local paths, hidden facts, or hidden
  coordinates, including `layout_updates`;
- legacy `generate_map_svg` behavior remains covered.

Commands:

```bash
python -m pytest -q tests/test_overview_topology_renderer.py tests/test_overview_topology_render_tools.py tests/test_tool_registry.py tests/test_prompt_projection.py tests/test_prompts.py::test_system_prompt_includes_shared_cycle_contract tests/test_prompts.py::test_system_prompt_prefers_overview_topology_renderer_before_llm_svg_fallback tests/test_prompts.py::test_user_prompt_routes_overview_map_requests_to_deterministic_renderer_hint tests/test_prompts.py::test_prompt_snapshot_projection_uses_safe_dm_map_view tests/test_map_core.py::test_player_and_dm_projection_filter_hidden_facts_and_raw_render_paths -p no:cacheprovider
python -m compileall -q astrbot_plugin_auto_trpg_dm tests
git diff --check
```

Adjust targeted test filenames to the final implementation files. Full test
suite remains desirable before PR, but Windows permission issues around pytest
runtime directories should be distinguished from product regressions.

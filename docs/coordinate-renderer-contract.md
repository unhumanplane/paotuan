# Coordinate Renderer Contract

This document defines the coordinate and layout contract for future
player-facing map renderers.

It builds on the projection consumer matrix in
[`docs/projection-consumer-matrix.md`](projection-consumer-matrix.md). That
handoff defines renderer input as `player_view + render request/envelope` and
renderer output as render refs, artifact metadata, and delivery metadata.

## Goal

Future SVG or PNG maps must be rendered views of structured map data. Rendered
artifacts are not source of truth for map facts.

The source of truth must remain in deterministic, saved records:

- concrete coordinates;
- boundaries;
- anchors;
- path points;
- connection records;
- visibility metadata;
- coordinate system metadata;
- map fact revisions;
- render-layout revisions.

The renderer must not infer or persist authoritative map facts from SVG pixels,
PNG dimensions, canvas bounds, or LLM-written visual output.

## Non-Goals

This contract does not:

- implement a strict-grid SVG renderer;
- implement an overview topology SVG renderer;
- migrate or retire `generate_map_svg`;
- redefine the renderer input/output boundary from the projection consumer
  matrix;
- introduce a full global coordinate system;
- make SVG or PNG a source of truth;
- treat canvas size as physical scale;
- define character facing, sight cones, or field-of-view-aware left/right.

## Coordinate Source Contract

Stored map facts must use structured coordinates or topology records. Vague
spatial phrases are not authoritative map facts.

Allowed stored fact shapes include:

- map-local point coordinates;
- rectangular or polygonal boundaries;
- named anchors;
- path-point lists;
- connection records between target IDs;
- visibility metadata;
- rule-scale metadata.

Examples of phrases that may appear in candidate input or projected narration,
but must not be stored as authoritative facts by themselves:

- `west side`;
- `left of`;
- `near`;
- `leads north`;
- `around the corner`;
- `somewhere behind the room`.

When natural language introduces these phrases, code may pass the phrase to a
bounded candidate-conversion step. The stored result still needs a structured
candidate event and deterministic validation before it becomes map state.

## Map-Local Coordinates

Version 1 uses map-local coordinate systems. Each coordinate-bearing map record
should declare enough metadata for later rendering and relation derivation:

| Field | Meaning |
|-------|---------|
| `coordinate_type` | Coordinate representation, such as `grid`, `cartesian`, or `topology_anchor`. |
| `origin` | Map-local origin definition. |
| `units` | Display-independent unit label, such as `cell`, `meter`, or `abstract_step`. |
| `rule_scale` | Gameplay scale metadata for movement, distance, and range interpretation. |
| `map_revision` | Revision of authoritative map facts. |
| `layout_revision` | Revision of render-layout placement, if layout records exist. |

Canvas width and height are display profile choices only. They must not become
world size, physical distance, or discovery state.

## Ownership Matrix

| Data | Owner | Valid Stored Shape | Consumers | Forbidden Use |
|------|-------|--------------------|-----------|---------------|
| Map facts | MapStore record | Coordinates, boundaries, anchors, path points, connection records, visibility, revision metadata | Map tools, projection helpers, relation service, renderer envelope builder | SVG pixels, vague prose, or LLM free text as authority |
| Strict tactical grid | MapStore strict record with legacy mirror | Grid width/height, cells, obstacles, entity positions, rule scale | Spatial tools, combat tools, compatibility loader | Future renderer reading legacy `battle.grid` as authority |
| Render layout | Code-owned layout record | Element IDs, display anchors, layout revision, source map revision, display profile | Deterministic renderers and delivery | Treating layout as map fact revision |
| Render refs | MapStore `render_refs` and delivery metadata | Artifact type, title, name, `visual_only`; internal path/url only in code-owned delivery fields | Safe projection, delivery | Exposing local path, URL, or raw SVG to player/ordinary prompt |
| Derived relations | Deterministic relation service | Direction, adjacency, distance band, visible exits, route candidates, target IDs | DM narration, RA authority, renderer labels, safe tool output | Raw hidden coordinates in player-facing output |
| Hidden coordinates | Backend MapStore only | Hidden coordinates or planning anchors with hidden/DM-only visibility | Code-owned planning and explicit authority views | Leaking via player bounds, empty space, grid extent, or layout |
| Agent candidates | Candidate event payload | Proposed coordinates, anchors, path points, or connections | Validator and human/code authority path | Direct map patching or visibility decisions |

## Derived Relation Contract

DM, RA, and renderer-facing consumers should use derived relation output instead
of raw hidden coordinates.

Expected relation fields:

- `source_id`;
- `target_id`;
- `relation_type`;
- `relative_direction`;
- `distance_band`;
- `visible_exits`;
- `route_candidates`;
- `blocked_or_unknown`;
- `source_map_revision`;
- `source_layout_revision`, when layout-dependent.

`distance_band` is preferred for ordinary player-facing output unless exact
coordinates are already player-visible. Relation output should use IDs and
safe labels so later renderers and narrators do not need to inspect hidden map
records.

If a relation depends only on hidden or unexplored backend coordinates, the
player-facing relation should be unknown or undiscovered. It must not reveal
hidden routes through spacing, bounds, empty areas, or inferred exits.

## Hidden Coordinate Leakage Guard

Player-facing rendering must not reveal hidden backend coordinates through
secondary visual signals.

Required guardrails:

- Build renderer input from `player_view` plus an explicit render envelope.
- Calculate player-visible bounds from visible elements and display profile, not
  from all backend coordinates.
- Do not let empty canvas space imply hidden room count, dungeon extent, route
  length, or unexplored exits.
- Do not expose hidden layout records before visibility changes.
- Do not expose local artifact paths, provider URLs, raw SVG, or raw PNG
  metadata through prompt/player facts.
- Keep diagnostic views and raw battle snapshots out of ordinary narration.
  Ordinary tactical state queries use safe battle/map summaries; raw grid
  inspection belongs to code-owned spatial or diagnostic paths.
- Keep map fact revisions separate from render-layout revisions.

## Legacy Compatibility Fields

The current codebase still has compatibility paths that later renderer work must
handle deliberately:

- `generate_map_svg()` is a legacy visual-only LLM SVG path.
- `scene["last_map_svg"]` is a legacy visual reference.
- `scene["_pending_outputs"]` is delivery infrastructure.
- `battle["grid"]` is a compatibility mirror and migration source, not future
  renderer authority.
- `get_battle_snapshot()` returns a prompt-safe `battle_status` /
  `tactical_map` summary, not raw `session.battle` or `grid.to_dict()`.

These fields must not be silently promoted into the coordinate contract. Later
migration or cleanup should happen in focused PRs.

## Agent-Code Boundary

Code owns:

- coordinate authority;
- schema validation;
- visibility checks;
- render bounds;
- relation derivation;
- map fact persistence;
- render-layout persistence;
- revision updates;
- artifact metadata;
- delivery side effects.

Agents or LLMs may only convert natural-language map descriptions into bounded
structured candidate events. They must not decide whether coordinates are
authoritative, whether hidden coordinates are safe to reveal, whether a legacy
mirror is current, or whether a render artifact should update map facts.

## Validation Matrix

| Case | Required Behavior |
|------|-------------------|
| Stored fact contains only vague spatial prose | Reject as authoritative map fact or store only as narration/candidate note. |
| Candidate event includes coordinates without type/origin/units context | Reject or require code/default metadata before persistence. |
| Renderer input requests raw MapStore or `battle.grid` | Reject; renderer input must be `player_view + render request/envelope`. |
| Renderer output contains SVG/PNG artifact | Store as render ref / delivery metadata only. |
| Player view has hidden coordinates outside visible region | Do not use those coordinates for player bounds, empty space, or layout. |
| Relation depends on hidden target | Return unknown/undiscovered relation for player-facing view. |
| Render-layout changes without map fact changes | Increment layout revision only. |
| Map fact changes without layout changes | Increment map fact revision; renderer may need a new layout pass. |
| LLM proposes map update | Treat as candidate event; validate before persistence. |

## Good / Base / Bad

Good:

- A room record stores boundaries, exits, visibility, coordinate metadata, and a
  map revision.
- A renderer envelope asks for the current map in `player_view` with a display
  profile, then writes only a render ref and delivery metadata.
- A derived relation says target B is an adjacent visible exit from target A
  with a short distance band and target IDs.

Base:

- A strict-grid map continues to mirror into `battle.grid` for compatibility,
  but future renderer contracts treat MapStore as authority.
- A legacy `last_map_svg` remains visible as a safe render ref title/name only.

Bad:

- A renderer reads `battle.grid` directly and treats it as the future authority
  contract.
- SVG canvas size is used as physical distance.
- Hidden backend rooms expand the player-visible canvas, revealing unexplored
  map extent.
- An LLM writes final coordinates directly into `maps` because a prompt told it
  to be careful.

## Tests Required For Later Runtime Work

This document is a contract and does not change runtime behavior by itself.
When later PRs implement the contract, add tests for:

- `player_view` omitting hidden coordinates, hidden layout records, raw grids,
  local paths, URLs, and raw SVG;
- derived relation output for visible exits, adjacency, distance bands, unknown
  hidden targets, and route candidates;
- map fact revision and render-layout revision separation;
- renderer envelope rejection of raw MapStore / `battle.grid` input;
- prompt/tool projection preventing renderer artifacts and raw battle snapshots
  from reaching ordinary DM narration;
- compatibility behavior for `last_map_svg`, `_pending_outputs`, and
  `battle.grid`.

Recommended targeted test areas:

- `tests/test_map_core.py`;
- `tests/test_prompt_projection.py`;
- `tests/test_prompts.py`;
- `tests/test_router_usage.py`;
- `tests/test_environment_agent.py`;
- `tests/test_spatial_tools.py`;
- `tests/test_strict_lifecycle_tools.py`.

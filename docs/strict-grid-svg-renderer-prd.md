# Strict Grid SVG Renderer PRD

## Current / Already Done

The repository already has the map ownership and strict-grid foundations needed
for a deterministic renderer:

- `GameSession.maps` is the code-owned MapCore store for map records, facts, and
  render references.
- `strict_local_map` records and `active_strict_map_id` identify the active
  strict map independently from combat lifecycle.
- `battle.grid` is a legacy migration source and compatibility mirror. It is not
  higher authority than MapCore strict grid.
- `GridState`, `SpatialEngine`, LOS helpers, and `MapCalculator` already own
  movement, distance, blocking, cover, and line-of-sight calculations.
- `project_map_store(..., "player_view")` is the player-facing projection
  boundary and filters hidden facts, raw grid payloads, raw paths, URLs, and raw
  SVG.
- `render_refs` are already visual-only references. They can identify rendered
  artifacts without becoming spatial facts.
- The existing SVG path has sanitization, file writing, PNG preview, chat
  attachment, and `_pending_outputs` infrastructure that can be reused where the
  renderer output fits the current contract.

The existing `generate_map_svg` path is still an LLM-written SVG generator. It is
a legacy visual fallback and migration asset for this task, not a source of map
truth.

## Implementation Notes

This PRD is now backed by these implementation entrypoints:

| Layer | Public name | Responsibility |
| --- | --- | --- |
| Renderer core | `astrbot_plugin_auto_trpg_dm.rendering.strict_grid_svg.render_strict_grid_svg()` | Render deterministic SVG from `StrictGridRenderInput` dataclasses. |
| Layout core | `calculate_strict_grid_canvas()` | Compute margin, header, legend, grid origin, canvas size, and cell pixel geometry. |
| Player-safe adapter | `build_strict_grid_render_input()` | Accept only `projection: "player_view"`, crop to `visible_bounds`, translate integer coordinates, and filter non-player-visible overlays. |
| Tool entrypoint | `render_strict_grid_svg` | Load active MapCore strict grid, migrate legacy-only `battle.grid` when needed, write SVG, add a visual-only render ref, and optionally enqueue `_pending_outputs` using `type: "svg_map"` plus `render_type: "strict_grid_svg"` for existing chat delivery compatibility. |

The renderer uses Python XML construction rather than LLM-written SVG/XML. It
does not call `generate_map_svg()` and does not write rendered SVG text back to
map facts or `grid`.

## Prerequisite Status

This task starts from current `upstream/main` after the coordinate renderer
contract and MapCore projection work have landed. The prerequisite boundary is:

- renderer input comes from `player_view` plus an explicit render request or
  envelope;
- renderer output is a visual artifact reference and delivery metadata;
- renderer may not read raw hidden MapStore facts, raw `battle.grid`, raw SVG, or
  write map facts;
- strict grid rendering uses structured map-local integer coordinates;
- SVG pixels are normalized display geometry and never become authoritative
  world distance.

## Impact Scan Evidence

The implementation must account for these current readers and writers:

| Area | Evidence | Impact |
| --- | --- | --- |
| `GridState` | `astrbot_plugin_auto_trpg_dm/spatial/grid.py` defines `Cell`, `Entity`, and `GridState`. | Renderer should consume grid-compatible structured coordinates without moving render logic into spatial models. |
| `SpatialEngine` | `astrbot_plugin_auto_trpg_dm/spatial/engine.py`, `spatial/los.py`, and `spatial/map_calculator.py` own movement, LOS, range, and route calculations. | Renderer can display calculation-relevant fields but must not recalculate or mutate adjudication state. |
| MapStore strict maps | `astrbot_plugin_auto_trpg_dm/core/map_core.py` defines `MAP_VIEW_PLAYER`, `strict_local_map`, `active_strict_map_id`, `load_active_strict_grid()`, `save_active_strict_grid()`, `migrate_legacy_battle_grid()`, `add_render_ref()`, and projection helpers. | Renderer input adapter should prefer MapCore strict grid and only use legacy fallback through existing migration helpers. |
| `battle.grid` compatibility | `spatial_tools.py` keeps a temporary legacy mirror and migrates legacy grids when no active strict grid exists. | Renderer contract should not consume `battle.grid` directly; migration is compatibility input only. |
| Legacy SVG generator | `tools/map_tools.py` exposes explicit fallback `generate_map_svg`, calls an LLM sub-context, sanitizes SVG, writes files, records visual-only render refs where possible, and appends normalized `_pending_outputs`. It no longer writes new `scene["last_map_svg"]`. | This task adds a deterministic strict-grid path; later final sweep keeps legacy SVG explicit-only and visual-only. |
| SVG sanitization and preview | `sanitize_svg()` lives in `tools/map_tools.py`; PNG preview and output attachment are handled in `main.py`. | Deterministic SVG should stay within supported sanitized SVG primitives and reuse delivery where safe. |
| `_pending_outputs` | `main.py` pops pending output records for chat delivery; `map_tools.py` and other tools can append records. | This task may reuse pending output records for renderer artifacts but does not change cadence or anti-spam state. |
| Prompt and tool schemas | `core/prompts.py` still asks for `generate_map_svg`; `tools/registry.py` still registers it and may add it to selected tools. | Normal map request routing migration is a later delivery/legacy PR; this task should only expose the minimal strict renderer path needed for deterministic rendering. |
| Tests | Existing tests cover MapCore projection, prompt projection, spatial engine, MapCalculator, prompts, and router behavior. | New tests should extend deterministic rendering, hidden leakage, compatibility, and metadata coverage without weakening existing legacy tests. |

## Strict Grid Renderer Contract

The strict-grid SVG renderer is a deterministic code path for tactical,
exploration, and puzzle maps that require precise local spatial understanding.

Hard requirements:

- Normal strict-grid rendering must not call an LLM to write SVG or XML.
- Integer grid coordinates are the source for cells, entities, doors, hazards,
  obstacles, cover, visible boundaries, and discovered areas.
- SVG geometry is computed from fixed layout inputs: margin, header height,
  legend height, grid width, grid height, and `cell_size`.
- Grid lines are always visible.
- The legend always declares per-cell distance or rule scale.
- Terrain, movement blocking, line-of-sight blocking, cover, doors, hazards,
  discovered areas, obstacles, and tokens are rendered from structured data.
- Hidden or unexplored facts are omitted, cropped, masked, or precision-reduced
  before SVG generation.
- SVG output and preview artifacts cannot write back to map facts.

`GridState-compatible` does not mean every rendered concept must be stored on
`GridState.Cell`. Core grid fields such as terrain, cost, movement blocking, LOS
blocking, cover, and entities can come from `GridState`. Other concepts such as
doors, hazards, discovered areas, visible boundaries, labels, and render hints
may enter through the player-safe render envelope as structured side channels.

## Input Contract

The renderer accepts a player-safe envelope derived from `player_view` and the
active strict map. It does not accept raw MapStore, raw `battle.grid`, raw SVG, or
DM-only facts.

Proposed envelope shape:

```json
{
  "map_id": "strict-local-map",
  "map_revision": 1,
  "projection": "player_view",
  "purpose": "strict_grid",
  "title": "Gatehouse",
  "grid": {
    "width": 12,
    "height": 8,
    "origin": "top_left",
    "unit": "cell",
    "rule_scale": {
      "distance_per_cell": 5,
      "unit": "ft",
      "label": "5 ft per cell"
    }
  },
  "visible_bounds": {
    "min_x": 0,
    "min_y": 0,
    "max_x": 11,
    "max_y": 7
  },
  "cells": [
    {
      "x": 0,
      "y": 0,
      "terrain": "stone",
      "blocks_move": false,
      "blocks_los": false,
      "cover": "none",
      "discovered": true,
      "visible": true
    }
  ],
  "entities": [
    {
      "id": "hero",
      "name": "Hero",
      "x": 1,
      "y": 2,
      "faction": "ally",
      "visible": true
    }
  ],
  "doors": [],
  "hazards": [],
  "obstacles": [],
  "labels": []
}
```

Validation rules:

- `map_id`, `projection`, `grid.width`, `grid.height`, `rule_scale`, and integer
  coordinates are required.
- Coordinates must be integers within the player-safe render bounds after
  cropping/masking.
- Elements with `visible: false`, hidden visibility, or missing player-safe
  projection approval are not accepted by the renderer.
- Renderer code may reject unsupported payloads with structured errors rather
  than asking an LLM to repair SVG.

## Output Contract

The renderer returns tool metadata and stores a render ref, not map facts. The
actual tool result shape is:

```json
{
  "ok": true,
  "map_id": "strict-local-map",
  "title": "Gatehouse tactical map",
  "file_path": "...",
  "file_name": "20260505_000000_Gatehouse_tactical_map.svg",
  "svg_chars": 4096,
  "send_to_chat": true,
  "visual_only": true,
  "render_ref": {
    "type": "strict_grid_svg",
    "title": "Gatehouse tactical map",
    "name": "20260505_000000_Gatehouse_tactical_map.svg",
    "visual_only": true
  }
}
```

Code-owned storage may keep a local path for file delivery. Projected consumer
views must not expose path, URL, raw SVG, raw grid, or hidden metadata. Prompt
projection keeps safe metadata such as `file_name`, `strict_grid_svg`, and
`visual_only`, while removing `file_path`, nested `path`, raw SVG, and raw grid
payloads.

## Player View / Hidden Fact Leakage Guard

The hidden-data guard must happen before SVG generation:

- The adapter builds a player-safe envelope from `player_view` and approved
  strict-grid data.
- Hidden cells, hidden labels, hidden hazards, hidden doors, hidden obstacles,
  hidden tokens, and hidden coordinates are not serialized into the envelope.
- If hidden backend coordinates would reveal future space through canvas size,
  blank regions, grid extent, or visible bounds, the adapter must crop, mask, or
  precision-reduce the output bounds.
- SVG text and element IDs must not contain hidden identifiers or labels.
- Tests must assert absence of hidden text and coordinates in both SVG output and
  projected render metadata.

## Compatibility Routing Matrix

| Existing path | Treatment in this task | Reason |
| --- | --- | --- |
| Active MapCore `strict_local_map` grid | Route to deterministic strict-grid renderer. | This is the authoritative strict map path. |
| Strict exploration map | Route to deterministic strict-grid renderer. | Strict maps are not combat-only. |
| Combat-linked strict map | Route to deterministic strict-grid renderer. | Combat uses the same strict map authority with battle lifecycle link. |
| `create_grid()` created default strict map | Route through MapCore strict adapter, then deterministic renderer. | `create_grid()` now writes MapCore strict grid while preserving legacy behavior. |
| Legacy-only `session.battle["grid"]` | Compatibility migration/fallback before rendering. | Legacy mirror is not the renderer contract. |
| Stale `battle.grid` mirror when MapCore strict grid exists | Ignore for renderer authority. | Stale legacy mirror must not override MapCore. |
| `get_battle_snapshot()` tactical state query | Returns safe `battle_status` / `tactical_map` summaries after 03.1.08 cleanup. | It is not renderer input and must not reintroduce raw `battle.grid` or raw MapStore grids into ordinary tool output. |
| `generate_map_svg()` | Was intentionally unchanged in the strict renderer task; later delivery/final-sweep work keeps it explicit-only. | It remains a legacy visual fallback, not normal strict renderer authority. |
| `scene["last_map_svg"]` | Was intentionally unchanged in the strict renderer task; final sweep stops new writes and keeps old records as read compatibility. | It is legacy visual state, not strict renderer truth. |
| `scene["_pending_outputs"]` | Reused only for renderer artifact delivery. | Cadence, duplicate suppression, and migration are owned by delivery/final-sweep work. |
| Existing SVG sanitizer | Reuse where safe. | Deterministic renderer can emit sanitizer-compatible SVG primitives. |
| Existing SVG-to-PNG preview | Reuse where safe. | Preview delivery is already implemented. |
| Chat delivery cadence | Intentionally unchanged. | Combat round cadence and map-request routing belong to later delivery migration. |
| Prompt rule asking for `generate_map_svg` | Intentionally unchanged or minimally coexists. | Normal tactical-map request migration is not part of this renderer core task. |
| Ambient image and non-map visual output | Intentionally unchanged. | This task is only strict-grid tactical rendering. |

## Delivery / Pending Output Plan

Renderer artifacts should use the existing delivery shape when possible:

- write deterministic SVG through a narrow renderer-owned wrapper under the
  repository maps directory;
- create a visual-only render reference for MapCore when the artifact is tied to
  a strict map;
- optionally append a pending output record compatible with current chat
  delivery. The pending record keeps `type: "svg_map"` so existing PNG preview
  attachment code consumes it, and adds `render_type: "strict_grid_svg"` to
  preserve renderer identity;
- keep delivery metadata separate from map facts and spatial state.

This strict renderer task did not implement:

- combat every-N-round automatic map send cadence;
- duplicate/spam suppression state;
- broad normal player map-request routing migration;
- global downgrade or hiding of `generate_map_svg`; later delivery/final-sweep
  work keeps it explicit-only;
- cleanup of legacy `last_map_svg` fields; final sweep stops new writes and
  keeps old records as compatibility metadata.

## SVG Sanitization / PNG Delivery Reuse Plan

The deterministic renderer emits only simple SVG elements through code-owned XML
construction:

- `svg`, `rect`, `line`, `circle`/`ellipse`, `polygon`/`polyline`, `text`, and
  grouped elements where accepted;
- inline deterministic styles limited to safe fill, stroke, opacity, and text
  properties;
- no scripts, external images, foreign objects, remote links, event handlers, or
  raw embedded provider payloads.

This task does not introduce a new PNG preview subsystem. If the existing chat
delivery path later converts strict-grid SVG to PNG, it should consume this
fixed SVG subset rather than accepting provider-authored XML.

## This Task Adds

- A deterministic strict-grid SVG renderer core in
  `astrbot_plugin_auto_trpg_dm/rendering/strict_grid_svg.py`.
- A player-safe strict-grid render envelope adapter in
  `astrbot_plugin_auto_trpg_dm/rendering/strict_grid_adapter.py`.
- Stable layout calculation for margin, header, legend, grid bounds, and
  `cell_size`.
- Rendering for visible grid lines, rule-scale legend, terrain, movement
  blockers, LOS blockers, cover, doors, hazards, discovered areas, obstacles,
  labels, and tokens.
- A minimal tool entrypoint in `tools/strict_grid_render_tools.py` registered as
  `render_strict_grid_svg`.
- Targeted tests for deterministic geometry, hidden leakage prevention,
  structured coordinate source, no LLM SVG generation, no SVG-to-fact writeback,
  delivery metadata projection, and legacy-only `battle.grid` compatibility.
- Public documentation for strict-grid renderer behavior and compatibility
  routing.

## Conflicts / Tensions

- `tools/map_tools.py` is already close to the file-size threshold, so the new
  renderer should not be added there. Keep renderer core in a new cohesive
  module.
- `tools/registry.py` crossed the soft 1200-line threshold by a minimal
  registration hunk. This is a short-term exception for tool exposure only; any
  future registry size work should split by registration responsibility or tool
  domain, not by generic constants/interfaces/utilities.
- `generate_map_svg` remains prompt-visible during this task. Replacing normal
  map request routing too early would merge renderer implementation with
  delivery migration.
- `player_view` currently filters raw grid payloads. The renderer adapter must
  bridge code-owned strict grid data into a player-safe envelope without handing
  raw hidden grid to LLM or player-facing projection.
- Existing preview fallback behavior may expose local delivery details in some
  failure cases. This is delivery hardening, not strict renderer core, unless a
  new renderer path directly triggers the leak.

## Out of Scope

- Overview topology rendering.
- Character-facing, facing-aware left/right, sight-cone, or private vision
  rendering.
- Perfect art style or rich illustrated battle maps.
- Manual map editing UI.
- Changing map facts from SVG, PNG, or rendered artifacts.
- Global `generate_map_svg` removal, hiding, or prompt migration; later
  delivery/final-sweep work keeps it explicit-only instead of deleting it.
- Delivery cadence and duplicate/spam suppression, later owned by delivery
  cadence work.
- Final legacy cleanup of `battle.grid`, `last_map_svg`, or old SVG state,
  later owned by 03.1.08 / 03.1.08.01.

## Purpose And Means Alignment

The purpose is to make strict tactical maps stable, player-safe, and repeatable.
The means is deterministic code rendering from structured coordinates.

This is not an art-quality feature and not a new source of map truth. The SVG is
a communication artifact. Spatial truth remains in MapCore strict maps,
`GridState`-compatible data, and deterministic spatial tools.

## Agent-Code Responsibility Split

Code owns:

- renderer input schema and validation;
- player-safe envelope construction;
- hidden-fact filtering, cropping, masking, and precision reduction;
- deterministic geometry and SVG element generation;
- file writing, sanitization, render refs, and delivery metadata;
- compatibility routing and fallback decisions;
- tests, audit-friendly metadata, and projection guards.

Agent or LLM may still help with:

- interpreting player language into structured candidate actions;
- proposing labels, icon kinds, or narrative descriptions under projected
  context;
- legacy fallback SVG generation only where the old path explicitly remains in
  use.

Agent or LLM must not:

- write normal strict-grid SVG/XML;
- read raw hidden map facts or raw strict grid for player-facing rendering;
- decide whether `battle.grid` overrides MapCore;
- mutate map facts from rendered output.

## Atomic Commit Plan

1. `docs(renderer): add strict grid SVG renderer PRD`
   - Adds this PRD, impact scan, routing matrix, and staged implementation plan.
   - No runtime behavior changes.
   - Verify with `git diff --check` and privacy/path scan.

2. `renderer(strict-grid): add deterministic SVG renderer core`
   - Adds a cohesive renderer module, geometry model, SVG builder, and unit
     tests for stable output, grid lines, and legend.
   - Does not touch `tools/map_tools.py` except imports only if unavoidable.

3. `renderer(strict-grid): build player-safe render envelope`
   - Adds adapter/validation for strict-grid render envelopes.
   - Tests hidden facts, hidden labels, hidden tokens, and hidden bounds.

4. `tools(strict-grid): expose minimal strict renderer path`
   - Adds the smallest tool or service entrypoint for deterministic strict-grid
     rendering.
   - Keeps `generate_map_svg` legacy behavior intact.
   - Tests coexistence and compatibility fallback.

5. `tests(renderer): cover delivery metadata and projection guard`
   - Covers visual-only metadata, render ref projection, no raw path/raw SVG/raw
     grid leak, and no SVG-to-fact writeback.

6. `docs(renderer): sync strict renderer contract notes`
   - Updates relevant public design docs after implementation reveals exact
     function names and payloads.
   - Does not upload local workflow notes.
   - Confirms `generate_map_svg` remains legacy fallback and delivery cadence is
     still out of scope.

## Work Rounds / Commit Checkpoints

Each round should finish before the next starts:

1. Check target code file line counts before editing code.
2. Implement only the current commit's intent.
3. Run targeted tests for that commit.
4. Run `git diff --check`.
5. Stage only the current commit's files or hunks.
6. Commit with a reviewable message.
7. Re-check `git status` before starting the next round.

If a file is near 1200 lines, plan a responsibility-based split before adding
substantial logic. If a file is near or above 1500 lines, split first or document
a short-term exception. Do not split modules only by "interfaces",
"constants", or "utilities"; split by responsibility, state boundary, domain
concept, or testable feature boundary.

## Acceptance Criteria

- Same player-safe strict-grid envelope renders to stable SVG geometry across
  repeated runs.
- SVG output contains visible grid lines.
- SVG legend declares per-cell distance or rule scale.
- Terrain, movement blocking, LOS blocking, cover, doors, hazards, discovered
  areas, obstacles, and tokens render from structured coordinate data.
- Hidden cells, hidden facts, hidden labels, hidden tokens, and hidden
  coordinates do not appear in SVG or projected metadata.
- Token and obstacle positions derive from integer grid coordinates, not SVG
  text, LLM output, or vague spatial phrases.
- Normal strict-grid render path does not call an LLM to write SVG/XML.
- SVG output cannot write back to map facts.
- Legacy `generate_map_svg` and legacy visual state remain compatible until the
  later delivery/legacy migration PR.

## Verification Plan

Targeted verification should include:

```powershell
python -m pytest -q tests/test_strict_grid_svg_renderer.py tests/test_strict_grid_render_envelope.py tests/test_strict_grid_render_tools.py tests/test_tool_registry.py tests/test_map_core.py tests/test_prompt_projection.py -p no:cacheprovider
python -m compileall -q astrbot_plugin_auto_trpg_dm tests
git diff --check
```

Additional checks:

- inspect generated SVG for hidden labels, hidden IDs, raw paths, URLs, and raw
  SVG echo fields;
- assert renderer tests do not monkeypatch or call LLM provider code;
- verify MapCore projection still strips raw render paths and raw grid payloads;
- verify any pending-output record remains visual-only and does not mutate map
  facts;
- run broader tests only after the minimal strict renderer path is wired into
  tool or delivery surfaces.

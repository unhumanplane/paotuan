# Map Request Hard Routing

This document records the runtime contract for explicit visual-map requests
after deterministic strict-grid and overview-topology renderers became the
normal map output path.

## Goal

When a player explicitly asks for a visual map, the DM flow must either:

- call a deterministic renderer and deliver the resulting `svg_map` attachment;
- or return a concise setup-needed response when structured player-view map
  data is missing.

The LLM must not replace that artifact with an ASCII table, emoji tile sketch,
Markdown grid, or prose-only "map" unless the player explicitly asked for a
text-only sketch.

## Non-Goals

- Do not redesign strict-grid geometry or overview topology layout.
- Do not remove the legacy `generate_map_svg` tool in this change.
- Do not make every spatial narration require a rendered map.
- Do not change `main.py` attachment mechanics or chat-client preview behavior.
- Do not let generated SVG, PNG, or text maps write authoritative map facts.

## Runtime Flow

1. The tool registry exposes deterministic map renderers for normal visual map
   requests. `generate_map_svg` remains hidden unless the player explicitly asks
   for fallback, legacy, style experiment, or migration behavior.
2. `IntentRouter` builds a code-owned guard context from the raw player message
   and the exposed tool names.
3. For an explicit visual-map request with a deterministic renderer available,
   the router requires at least one renderer attempt before accepting a final
   text response.
4. If the first LLM response skips the renderer, the router performs one bounded
   retry that asks for the deterministic renderer instead of an ASCII/table/text
   substitute.
5. If no renderer is attempted after the retry, or a renderer reports stable
   missing map data, the router returns a setup-needed response.
6. If the renderer succeeds and the final completion is empty or generic, the
   player receives `地图已生成，已附上。`; `_pending_outputs` still owns the actual
   attachment delivery.

## Text-Only Override

Explicit text-only map requests are allowed. Examples include requests that ask
for ASCII, text-only, text sketch, or no image/SVG/rendering. In that case the
router does not require renderer use and the registry avoids adding map renderer
tools only for that request.

This override is intentionally narrow. A normal request such as "draw the battle
map" or "show my current route" stays on the deterministic renderer path.

## Text-Map Guard

The guard suppresses text-map-looking completions only when:

- the player made an explicit visual-map request;
- the player did not request text-only output;
- no deterministic renderer succeeded.

Positive signals include ASCII box grids, box-drawing grids, repeated emoji tile
rows, and self-described map/layout text combined with tabular or compact grid
rows. The detector deliberately avoids blocking ordinary spatial narration, dice
summary tables, player rosters, diagnostic output, and setup-needed responses.

## Missing Data Response

When a visual map cannot be rendered because structured player-view map data is
missing, the response should stay short and should not invent a text map:

```text
现在还不能生成可靠的可视化地图：当前缺少可渲染的结构化地图数据。请先建立地图、放置关键实体或补齐区域拓扑后再请求生成地图。
```

The response must not expose hidden map facts, raw map store data, local artifact
paths, raw SVG, provider URLs, prompts, or audit details.

## Audit And Privacy

The guard writes stable audit records with:

- `type: "visual_map_request_guard"`;
- action and reason codes;
- renderer attempt/success/missing-data booleans;
- renderer tool names;
- missing-data error codes;
- text-map signal names;
- completion length and a short hash when a completion was inspected.

Audit and private logs must not include raw completion text, hidden map payloads,
raw SVG, provider URLs, local paths, or prompt bodies. The hash is only for local
correlation when debugging a suppressed response.

## Compatibility

The existing pending-output delivery bridge remains unchanged:

- deterministic renderers enqueue `type: "svg_map"` records;
- `render_type` preserves `strict_grid_svg` or `overview_topology_svg`;
- `path` remains internal delivery metadata;
- prompt projection and player-facing text must not reveal local paths;
- empty renderer completions still rely on the existing map-delivery
  acknowledgement and attachment path.

Legacy `generate_map_svg` is still available as explicit fallback or style
experiment behavior, but it cannot satisfy ordinary visual-map requests.

## Validation

Focused tests should cover:

- renderer retry when the LLM skips tools for a visual-map request;
- replacement of text-map bypasses with the setup-needed response;
- explicit text-only override;
- renderer missing-data handling;
- generic renderer success completion replacement;
- false-positive avoidance for non-map tables and ordinary narration;
- registry exposure that hides deterministic renderers for explicit text-only
  map requests and keeps `generate_map_svg` hidden for ordinary map requests.

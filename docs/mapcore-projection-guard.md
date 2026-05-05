# MapCore 投影护栏设计

本文说明 paotuan 的 MapCore 骨架、角色投影、候选地图事件边界和 legacy `battle.grid` adapter。它的核心目的不是替换现有战棋引擎，而是在地图事实进入 DM prompt、Recorder Agent 简称 RA、诊断快照或后续地图工具之前，先建立一层可测试的 code-owned contract。

## 目标

- 在 `GameSession` 上提供一等 `maps` store，用于保存地图记录、地图事实、视觉渲染引用和归档身份。
- 在 code 层提供稳定的投影 API，让不同角色只能看到自己允许消费的地图视图。
- 防止 DM / RA / LLM 读取 raw map store、隐藏地图事实、本地文件路径、provider URL 或 raw SVG。
- 让 RA 或其它 agent 只能提出 candidate map event，由本地 code 校验；agent 不能直接 patch 权威状态。
- 为严格小尺度空间裁定提供 `strict_local_map` record 和 active strict grid adapter，让现有战棋工具逐步从 legacy `battle.grid` 迁移到 MapCore。
- 保持旧存档兼容：缺少 `maps` 字段的存档会加载为空 MapCore store。

## 非目标

- 不在 03.1.01 阶段迁移现有 `session.battle["grid"]`；03.1.02 只提供 adapter / migration wrapper，不做最终清理。
- 不在 03.1.02 中彻底移除 legacy `session.battle["grid"]` mirror。
- 不重写 `spatial/` 下的坐标、移动、距离、视线、掩体和路径逻辑。
- 不引入 MapCalculator 或完整 map-aware spatial routing；这属于后续工具路由任务。
- 不改变 `tools/map_tools.py` 的 SVG / PNG 地图生成语义。
- 不让 RA 获得工具访问权或直接写入地图 store。
- 不把 `hidden` 地图事实暴露给 DM prompt、RA prompt 或玩家视图。
- 不引入新的持久化后端、数据库迁移或外部地图服务。
- 不引入 downstream-readable meta-rule fields、rule scale strictness profile 或 4.4 所需的规则尺度元数据；涉及 strictness / rule scale 的下游读取仍是后续前置工作。

## 当前边界

`strict_local_map` 是 MapCore record 的 `type`，不是新的顶层 session 字段。当前活动 strict map 由 `active_strict_map_id` 指向。strict map record 可以保存 raw `grid`，供本地 spatial tools 读取和写入；raw `grid` 不进入 DM / RA / player 投影。

`battle.grid` 现在是 legacy migration source 和 compatibility mirror，不再高于 MapCore strict grid。读取 strict grid 时，如果 `active_strict_map_id` 指向的 record 已有 `grid`，MapStore 是权威来源；legacy mirror 即使过期也不能覆盖它。如果没有 active strict map grid，但旧存档里仍有 `session.battle["grid"]`，adapter 可以把它迁移成 `type: "strict_local_map"` 的 record，并记录 migration source 和 authority assumption。

坐标、实体位置、障碍、视线、距离、掩体、回合顺序和行动结算仍由 `spatial/`、`battle` 和相关工具负责。03.1.02 只是把 strict grid 的读写入口接到 MapCore，不改变底层空间规则。

MapCore store 负责更高层的地图元数据和语义事实，例如“当前概览地图是哪张”“某张地图有哪些已公开线索”“某张视觉图对应哪个地图记录”。它不替代战棋网格，也不作为绕过 spatial 校验的入口。

SVG / PNG 地图和氛围图片都属于视觉辅助。视觉引用可以被记录为 `render_refs`，但它们不能自行改写地图事实。对 LLM 可见的 render ref 只保留安全描述字段，不包含本地 path、URL 或 raw SVG。

strict-grid SVG renderer 也是视觉辅助。`render_strict_grid_svg` 从
MapCore strict grid 和 `player_view` envelope 生成 deterministic SVG，
并用 `render_refs` 记录 `type: "strict_grid_svg"`、`name`、`title` 和
`visual_only`。本地 `path` 只服务文件交付；DM prompt projection 和
player-facing projection 不消费 raw `path`、raw SVG 或 raw `grid`。

## Strict Map Lifecycle 与 Combat Lifecycle

strict map lifecycle 是 MapCore record 上的 code-owned 状态，不由 LLM、prompt 文字或 `GameMode.TACTICAL` 推断。当前 lifecycle 取值：

| Lifecycle | 含义 |
| --- | --- |
| `inactive` | strict map 存在但不是当前活动严格地图。 |
| `active_exploration` | strict map 可用于探索、潜入、解谜、位置追踪或战前布置；不代表战斗进行中。 |
| `active_combat_linked` | strict map 已通过 `battle.map_id` 链接为当前战斗地图。 |
| `paused` | strict map 暂停保留，后续可恢复。 |
| `archived` | strict map 已归档；保留 archive identity，不销毁旧事实。 |

核心边界：

- `battle.active` 只表达 combat active，不能继续表达 strict map active。
- `battle.map_id` 只负责把当前 combat 链接到某个 `strict_local_map` record；结束战斗要解除这个链接，但不能删除 strict map。
- `active_strict_map_id` 负责当前 strict map 选择，不等于战斗正在进行。
- `create_strict_map` 只创建或重置 active strict map，并把 lifecycle 设为 `active_exploration`；它不会设置 `battle.active`。
- `start_combat_on_map` 把已有 strict map 转为 `active_combat_linked`，并由 code 设置 `battle.active = True`、`battle.map_id = <map_id>`。
- `end_combat` 把 combat 停止，清空 `battle.map_id`，把 strict map lifecycle 恢复为 `active_exploration`，并保留 grid / facts / render refs。
- `create_grid` 保留 legacy 兼容语义：它仍创建默认 strict map、进入 tactical mode，并启动 combat。新流程应优先使用 `create_strict_map` + `start_combat_on_map`。
- prompt projection、mode detection 和 ambient-image gate 通过 code helper 判断 combat active；`GameMode.TACTICAL` 或 active strict map 本身都不再等同于 combat active。

## Store Schema

`GameSession.maps` 由 `core/map_core.py` 管理，默认结构如下：

```json
{
  "schema_version": 1,
  "active_overview_map_id": "",
  "active_strict_map_id": "",
  "records": {},
  "archive_identity": {}
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `schema_version` | MapCore store schema 版本。当前为 `1`。 |
| `active_overview_map_id` | 当前概览地图记录 ID。用于剧情、区域和大范围位置理解。 |
| `active_strict_map_id` | 当前严格战棋地图记录 ID。用于与 `battle.grid` 对齐。 |
| `records` | 以 `map_id` 为 key 的地图记录集合。 |
| `archive_identity` | 跨归档、导出或未来迁移时使用的 store 级身份信息。 |

地图记录结构：

```json
{
  "id": "strict-local-map",
  "record_version": 1,
  "type": "strict_local_map",
  "title": "Battle grid",
  "authority": "spatial",
  "visibility": "dm",
  "grid": {},
  "facts": [],
  "render_refs": [],
  "archive_identity": {
    "migration_source": "legacy_battle_grid",
    "authority_assumption": "legacy_battle_grid_until_strict_map_exists",
    "strict_grid_adapter_version": 1
  },
  "created_at": "2026-05-04T00:00:00+00:00",
  "updated_at": "2026-05-04T00:00:00+00:00"
}
```

`type` 目前主要使用：

| Type | 含义 |
| --- | --- |
| `overview_map` | 概览地图，用于区域、剧情、路线和公开/DM 语义事实。 |
| `strict_local_map` | 严格本地地图，用于小尺度空间裁定，可承载 `grid`。 |

旧值 `strict` 会在 normalize 阶段归一化为 `strict_local_map`。

`grid` 只保存在 code-owned record 中。projection 会保留 record 的安全元数据、facts 和 render refs，但不会把 raw `grid` 输出给 DM prompt、RA authority snapshot 或玩家视图。

地图事实结构：

```json
{
  "id": "north-gate",
  "kind": "terrain",
  "text": "北门内侧有倒塌的马车形成半掩体。",
  "payload": {},
  "authority": "code",
  "visibility": "dm",
  "source": "dm_note",
  "created_at": "2026-05-04T00:00:00+00:00"
}
```

渲染引用结构：

```json
{
  "type": "svg_map",
  "title": "废城外环 SVG",
  "name": "outer-ring.svg",
  "path": "...",
  "url": "...",
  "visual_only": true,
  "created_at": "2026-05-04T00:00:00+00:00"
}
```

`path` 和 `url` 可以存在于 code-owned store 中，供本地物化、恢复或调试使用；它们不会进入 DM / RA / player 投影视图。

## Visibility 与 Authority

可见性取值：

| Visibility | 可见范围 |
| --- | --- |
| `public` | 玩家、DM、RA 都可消费。 |
| `player` | 玩家、DM、RA 都可消费，语义上属于玩家已知事实。 |
| `dm` | DM narration 和 RA authority 可消费，玩家视图不可见。 |
| `hidden` | 仅 code-owned store 可见，不进入 DM / RA / player 投影。 |
| `diagnostic` | 仅诊断用途；普通角色投影不消费。 |

权威来源取值：

| Authority | 含义 |
| --- | --- |
| `code` | 本地代码或工具确认的地图事实。 |
| `spatial` | 来自现有 spatial/grid 或战棋逻辑的事实。 |
| `dm` | DM 已确认并写入的地图事实。 |
| `ra_candidate` | RA 或 agent 提出的候选事实，必须经过 code 校验后才能应用。 |
| `visual` | 来自视觉渲染或地图图片的辅助引用，不是规则事实。 |

## Role Projection

所有进入 LLM prompt 或面向玩家的地图数据，都必须先经过 `project_map_store()` 或 `project_active_map_record()`。

| View | 调用方 | 可见 visibility | 额外限制 |
| --- | --- | --- | --- |
| `player_view` | 未来玩家可见地图或 UI | `public`, `player` | 不含 `dm` / `hidden` facts，不含 raw render path 或 URL。 |
| `dm_narration_view` | `core/prompts.py` 的 DM snapshot | `public`, `player`, `dm` | 不含 `hidden` facts，不含 raw render path 或 URL。 |
| `ra_authority_view` | `core/environment_agent.py` 的 RA authority snapshot | `public`, `player`, `dm` | 不含 `hidden` facts，不含 raw render path 或 URL；RA 仍不能直接写 store。 |
| `diagnostic_view` | 诊断快照 | 计数型视图 | 只暴露 record count、fact count、hidden fact count、render ref count，不暴露 fact payload。 |

当前 prompt 集成点：

- `_diagnostic_snapshot()` 会注入 `diagnostic_view`，并且只在存在地图记录时写入 `snapshot["maps"]`。
- `_project_snapshot_for_profile()` 会向 DM prompt 注入 `dm_narration_view`。
- `build_ra_authority_snapshot()` 会向 RA authority snapshot 注入 `ra_authority_view`，再经过 RA payload sanitizer。

## State Ownership 与 Prompt Projection

03.1.06 后，snapshot projection 不只按“字段大小”裁剪，还按状态所有权裁剪。核心规则是：map 只拥有空间事实，ordinary DM narration 只能读取 code 投影后的视图，不能让 LLM 自行判断 raw 或 hidden backend facts 是否可以进入叙事。

| Owner | 拥有字段 | Prompt 边界 |
| --- | --- | --- |
| map-owned | positions、spatial occupancy、blockers、hazards、visibility、coordinates、zones、strict grid raw payload | 只能通过 `project_map_store(..., dm_narration_view)`、`player_view`、`ra_authority_view` 或 `diagnostic_view` 进入对应消费者；ordinary DM snapshot 不读取 raw MapStore 或 legacy `battle.grid`。 |
| character-owned | HP、abilities、equipment、character cards、character tags | 通过 character projection 进入 DM/RA；空间坐标不归 character snapshot 自行解释。 |
| battle-owned | initiative、rounds、turn/action state、`battle.active`、combat lifecycle、`battle.map_id` link | DM battle projection 保留 combat/turn/link 状态；不携带 raw `grid`、entities 坐标或 obstacle payload。 |
| owner/control-owned | who controls what、player/GM/NPC ownership、player-character binding | 保留 `participants`、`player_character_map` 和必要 owner id；LLM 不能用 map entity tags 自行扩权。 |
| rule-owned | reusable rules、dice/math procedures、registered rule packages | ordinary DM 可读规则摘要或工具结果；raw rule packages 不直接进入 ordinary narration。 |
| legacy/ambiguous | `session.battle["grid"]` mirror、旧存档迁移字段、tool audit traces | 暂时保留给 spatial tools、migration 和诊断；后续 03.1.08 或 focused tool-schema PR 再清理公共工具 shape。 |

| Projection | 消费者 | 允许内容 | 禁止内容 |
| --- | --- | --- | --- |
| `player_view` | renderer / player-facing map data | player-safe map facts 和安全 render refs | `dm` / `hidden` facts、raw path、URL、raw grid。 |
| `dm_narration_view` | ordinary DM prompt | public/player/dm map facts、安全 render refs、battle-owned turn/link state | raw MapStore、hidden map facts、legacy `battle.grid`、raw strict grid、tool traces、raw RA output、web grounding payload、raw rule packages。 |
| `ra_authority_view` | RA authority analysis | 清洗后的 authority snapshot、DM-visible map facts、battle/character/rule authority摘要 | hidden map facts、raw prompt、raw player input、raw audit、raw strict grid。 |
| `diagnostic_view` | debug / tests / explicit diagnostics | counts、record metadata、projection telemetry | fact payload、hidden text、raw grid payload。 |
| raw store / hidden facts | code-owned local state only | deterministic tools、validators、migration adapters | ordinary DM narration、player-facing renderer、RA prompt。 |

## Strict Grid Renderer Consumer Boundary

`render_strict_grid_svg` 是 `player_view` 的 code-owned consumer，不是新的
map authority。它的读取和写入边界如下：

| Step | Code path | Allowed | Blocked |
| --- | --- | --- | --- |
| Load strict grid | `load_active_strict_grid(session.maps, session.battle)` | Active MapCore `strict_local_map.grid`; legacy-only `battle.grid` as migration source. | Letting stale `battle.grid` override an existing MapCore strict map. |
| Build player-safe envelope | `project_active_map_record(..., MAP_VIEW_PLAYER, strict=True)` plus renderer adapter | `projection: "player_view"`, player/public visibility, integer coordinates, visible bounds, structured overlays. | `dm` / `hidden` overlays, raw hidden facts, diagnostic records, non-player projections. |
| Render SVG | `build_strict_grid_render_input()` and `render_strict_grid_svg()` | Deterministic XML from structured coordinates, visible grid lines, rule scale legend, terrain, blockers, cover, doors, hazards, obstacles, labels, tokens. | LLM-written SVG/XML, remote images, scriptable SVG features, hidden labels or hidden coordinates. |
| Persist artifact | `add_render_ref(..., ref_type="strict_grid_svg", visual_only=True)` and optional `_pending_outputs` record | Visual-only metadata and local file delivery state. | Writing SVG, PNG, path, or rendered geometry back into `facts` or `grid`. |
| Prompt projection | `project_tool_results_for_dm_prompt()` | Safe fields such as `file_name`, `strict_grid_svg`, `visual_only`, title, and non-sensitive counts. | `file_path`, nested `path`, `url`, raw `grid`, raw SVG, hidden payloads. |

The renderer may load raw strict-grid state inside code to produce a visual
artifact, but its player-facing envelope still has to pass the same visibility
and projection guards as other MapCore consumers. SVG output is never parsed
back into facts and is never treated as authoritative map state.

## Legacy Battle Grid Adapter

03.1.02 引入的 adapter 是 code-owned migration layer，目的只是把现有 `battle.grid` 入口接到 MapCore strict map contract。

核心 helper：

| Helper | 责任 |
| --- | --- |
| `load_active_strict_grid(store, legacy_battle=None)` | 优先读取 `active_strict_map_id` 指向的 MapCore strict grid；没有 strict grid 时才返回 legacy `battle.grid` fallback。 |
| `save_active_strict_grid(store, grid, ...)` | 创建或更新 `type: "strict_local_map"` record，写入 raw `grid`，并更新 `active_strict_map_id`。 |
| `migrate_legacy_battle_grid(store, battle, ...)` | 将 legacy `battle.grid` 包装为 strict map record；如果 MapStore 已有 strict grid，则不迁移、不覆盖。 |

读取优先级：

1. `GameSession.maps.records[active_strict_map_id].grid`
2. legacy `session.battle["grid"]`，仅当 MapStore 没有 active strict grid 时使用
3. 无 grid，返回明确错误

`tools/spatial_tools.py` 现在通过 adapter 写 strict grid：

- `create_grid()` 作为 legacy 兼容入口，固定创建或重置 `DEFAULT_STRICT_LOCAL_MAP_ID` 对应的 `strict_local_map` record，并把 `session.battle["map_id"]` 指向该 record。
- `create_strict_map()` 是新的 strict lifecycle 入口，只创建 active exploration strict map，不设置 `battle.active`，也不建立 combat link。
- `start_combat_on_map()` 是新的 combat link 入口，只能链接已有 strict map；它设置 `battle.active`、`battle.map_id` 和 `active_combat_linked` lifecycle。
- `end_combat()` 是新的 combat unlink 入口；它停止 combat、清空 `battle.map_id`，但保留 strict map 并恢复 `active_exploration`。
- `create_grid()` 会在 record `archive_identity` 中记录 `source: "spatial_tool_create_grid"` 和 authority assumption，便于后续区分 MapStore 创建与 legacy migration。
- `place_entity()` 和 `move_entity()` 读取 MapCore strict grid，结算成功后写回 MapCore。
- `move_entity()` 和 `check_attack_vector()` 现在通过内部 `MapCalculator` 路由执行 deterministic spatial calculation。公开工具名、参数 schema、turn guard、audit、MapStore load/save 和 legacy mirror 仍由 `SpatialTools` 维护。
- `session.battle["grid"]` 暂时保留为兼容 mirror，供旧调用方和过渡期存档继续工作；普通 exploration strict map 写入只同步 mirror，不会把 `battle.active` 重新置为 true。
- 旧存档第一次通过 spatial tool 读取 legacy grid 时，会迁移到 MapCore，并保存 `map_id` 和 mirror。

authority guard：

- MapStore strict grid 已存在时，legacy mirror 不能覆盖它。
- migration metadata 写入 record `archive_identity`，用于后续排查来源与清理。
- agent / LLM 不参与“是否迁移”“哪个 grid 是权威”的判断。
- RA / DM / player 只能看到投影后的 map view，不能读取 raw strict grid 或 raw store。

## MapCalculator Routing

03.1.04 引入的 `MapCalculator` 是 code-owned calculation route，不是新的 LLM 工具。它的当前职责很窄：

- 按 operation、map type、rule scale、strictness、purpose、map id 等 route metadata 选择 deterministic calculator。
- 对 `strict_local_map` / grid-like strict maps，委派现有 `SpatialEngine` 计算 movement、distance、line of sight、range、blocking 和 cover。
- 对当前不支持的非 strict / 非 grid route 返回结构化 `unsupported_map_calculator_route`，而不是让 LLM 判断。

边界：

- `MapCalculator` 不读写 repository，不迁移 legacy grid，不保存 session，也不写 audit。
- `SpatialTools` 继续负责 `load_active_strict_grid()` / `save_active_strict_grid()`、turn actor guard、audit、session 保存和 legacy `battle.grid` mirror。
- 公开 LLM-callable 工具名仍是 `move_entity` 和 `check_attack_vector`；`MapCalculator` 只改变内部 ownership，不改变 prompt-facing schema。
- `place_entity()` 暂不迁移到 `MapCalculator`，它属于 map setup / entity placement 后续阶段。
- parent map plan 中的 `provisional` / `established` / `locked` authority-state lifecycle 仍是后续 schema work。当前 `authority` 字段仍表示 source owner，例如 `code`、`spatial`、`dm` 或 `ra_candidate`。

## Candidate Map Event

agent / LLM 不能直接修改 `GameSession.maps`。它们只能提交 candidate map event，并由 `validate_candidate_map_event()` 做结构化校验。该函数只返回校验结果，不会 mutate store。

允许的 candidate event：

| Event | 必要条件 | 输出 payload |
| --- | --- | --- |
| `create_map_record` | `map_id` 必填；visibility 只能是 `public`、`player`、`dm` | `title`, `map_type`, `visibility` |
| `add_fact` | `map_id` 必须已存在；`fact_id` 或 `id` 必填；`kind` 必填 | `fact_id`, `kind`, `text`, `payload`, `visibility` |
| `link_render_ref` | `map_id` 必须已存在；`ref_type` 或 `type` 必填 | `ref_type`, `title`, `name`, `visual_only` |
| `set_active_map` | `map_id` 必须已存在；`overview` 或 `strict` 至少一个为 true | `overview`, `strict` |

候选事件允许携带 `source` 和 `confidence`。`confidence` 会被裁剪到 `0.0..1.0`。

拒绝矩阵：

| Reason | 触发条件 |
| --- | --- |
| `invalid_candidate_type` | candidate 不是 dict。 |
| `raw_patch_not_allowed` | candidate 任意嵌套层包含 `maps`、`raw_map_store`、`raw_store`、`state_patch`、`patch`、`direct_patch`。 |
| `unsupported_event_type` | `event_type` / `type` 不在允许列表。 |
| `map_id_required` | 缺少 `map_id`。 |
| `invalid_payload_type` | `payload` 不是 dict。 |
| `unknown_map_id` | 非 create 事件引用不存在的 map record。 |
| `candidate_visibility_not_allowed` | candidate 要写入 `hidden` 或 `diagnostic` 可见性。 |
| `fact_id_required` | `add_fact` 缺少 `fact_id` / `id`。 |
| `fact_kind_required` | `add_fact` 缺少 `kind`。 |
| `render_ref_type_required` | `link_render_ref` 缺少 `ref_type` / `type`。 |
| `active_slot_required` | `set_active_map` 没有指定 `overview` 或 `strict`。 |

## 数据流

```text
code / tools
  -> GameSession.maps
  -> normalize_map_store()
  -> project_map_store(view)
  -> DM prompt / RA authority snapshot / player-facing map view
```

```text
RA / LLM proposal
  -> candidate map event
  -> validate_candidate_map_event()
  -> validated candidate result
  -> future code-owned apply path
```

第一条路径是权威状态投影。第二条路径是候选事件校验。两条路径不能合并：投影不写状态，candidate validation 也不直接写状态。

## Agent-Code Responsibility Split

code 负责：

- map schema、默认值、归一化和旧存档兼容；
- `strict_local_map` record 类型、`active_strict_map_id` 指针和 strict grid adapter；
- legacy `battle.grid` fallback、migration、authority precedence 和 compatibility mirror；
- map record / fact / render ref 的写入 helper；
- visibility、authority、ID、长度、JSON-safe payload 校验；
- DM、RA、player、diagnostic 四类投影视图；
- candidate event allowlist、blocked key 检查和错误矩阵；
- 未来将 validated candidate 应用到 store 的显式工具或服务层。

agent / LLM 只能负责：

- 基于投影后的地图视图理解当前场景；
- 生成叙事、总结或建议；
- 在需要改变地图语义时提出 candidate event。

agent / LLM 不能负责：

- 读取 raw `GameSession.maps`；
- 读取 raw strict grid 或 legacy `battle.grid`；
- 读取或推断 `hidden` facts；
- 读取本地 path、provider URL 或 raw SVG；
- 判断 `battle.grid` 是否应该迁移到 `strict_local_map.grid`；
- 直接 patch `maps`、`battle.grid` 或其它权威状态；
- 绕过 candidate validation 写入地图事实。

## 验证

推荐本地验证：

```powershell
python -m pytest -q tests/test_map_core.py tests/test_spatial_tools.py tests/test_prompts.py tests/test_environment_agent.py -p no:cacheprovider
python -m compileall -q astrbot_plugin_auto_trpg_dm tests
git diff --check
```

覆盖重点：

- 旧存档加载时自动补默认 `maps` store。
- map helper 会 copy-out 返回结果，不让调用方靠返回对象偷改 store。
- strict map lifecycle 可以在 `active_exploration`、`active_combat_linked`、`paused`、`archived` 间转换，且不丢失 grid。
- `create_strict_map()` 创建 active exploration strict map 时不启动 combat。
- `start_combat_on_map()` 通过 `battle.map_id` 把 combat 链接到已有 strict map。
- `end_combat()` 结束 combat 后保留 strict map，并解除 `battle.map_id` 链接。
- prompt projection、mode detection 和 ambient-image gate 不再把 `GameMode.TACTICAL` 或 active strict map 本身当成 combat active。
- `player_view`、`dm_narration_view`、`ra_authority_view` 都过滤 `hidden` facts。
- render ref 投影不暴露 `path` 或 `url`。
- `diagnostic_view` 只暴露计数，不暴露 fact payload。
- DM prompt 和 RA authority snapshot 只接收投影后的 `maps`。
- strict map record 可以在 store 内保存 raw `grid`，但 DM prompt 和 RA authority snapshot 不暴露该字段。
- `load_active_strict_grid()` 优先返回 MapStore strict grid，legacy `battle.grid` 只作为 migration fallback。
- `save_active_strict_grid()` 写入 `strict_local_map` record 并更新 `active_strict_map_id`。
- `migrate_legacy_battle_grid()` 不覆盖已经存在的 MapStore strict authority。
- `create_grid()` 固定重置默认 strict local map、写入 auditable source，并保持旧兼容语义：同步 `battle.map_id`、legacy mirror，并启动 combat。
- `place_entity()` 和 `move_entity()` 通过 adapter 写回 MapCore strict grid，并暂时维护 legacy mirror；非 combat linked 的 strict map 写入不能重新激活 combat。
- `move_entity()` 和 `check_attack_vector()` 通过 `MapCalculator` 路由到 `SpatialEngine`，且成功 result shape 与旧工具结果保持兼容。
- `check_attack_vector()` 和 `move_entity()` 都优先使用 MapStore strict grid；stale legacy mirror 不能影响攻击距离、视线或移动结果。
- candidate event 只做 validation，不 mutate store。
- raw patch、hidden visibility、未知 map、缺字段事件都会被拒绝。

## 后续扩展点

- 为 validated candidate 增加 code-owned apply 工具，并继续保持“validate 与 apply 分离”。
- 扩展 MapCalculator 支持 ruler distance、zone bands、topology maps 或 puzzle-specific calculators。
- 后续再决定是否把 `place_entity()`、`create_grid()` 和 map setup lifecycle 迁入 calculator / lifecycle route。
- 为 strict lifecycle 增加 pause / resume / archive 的 LLM-callable 工具边界；当前 MapCore helper 已支持 lifecycle 字段，但公开工具只覆盖 create / start combat / end combat。
- 补齐 4.4 downstream-readable meta-rule fields，让 strictness / rule scale 能被 MapCalculator、prompt projection 和规则工具以结构化字段读取，而不是从自然语言推断。
- 扩展 ownership snapshot 和 projection，把 map / character / battle / rule ownership 边界固定下来。
- 在最终 cleanup 阶段移除或降级 `battle.grid` mirror，只保留旧存档 migration loader。
- 为玩家 UI 或消息输出接入 `player_view`。
- 为地图归档、导出和跨版本迁移补充 `archive_identity` 规则。
- 在 SVG map 生成完成后自动写入 `render_refs`，但仍保持视觉引用不改写事实。

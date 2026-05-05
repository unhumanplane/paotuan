from astrbot_plugin_auto_trpg_dm.spatial.grid import Cell, Entity, GridState
from astrbot_plugin_auto_trpg_dm.spatial.map_calculator import MapCalculationRoute, MapCalculator


def test_map_calculator_delegates_move_entity_result_shape_to_strict_grid_engine():
    grid = GridState.empty(width=5, height=5)
    grid.entities["pc"] = Entity(id="pc", name="PC", x=1, y=1, move_points=6)
    route = MapCalculationRoute(
        operation="",
        map_type="strict_local_map",
        rule_scale="grid_5ft",
        strict=True,
        purpose="combat",
        map_id="strict-room",
    )

    result = MapCalculator(grid, route).move_entity("pc", 2, 1)

    assert result == {
        "ok": True,
        "entity_id": "pc",
        "from": {"x": 1, "y": 1},
        "to": {"x": 2, "y": 1},
        "path": [{"x": 1, "y": 1}, {"x": 2, "y": 1}],
        "cost": 1,
        "remaining_move_points": 5,
    }
    assert grid.entities["pc"].x == 2


def test_map_calculator_delegates_attack_vector_result_shape_to_strict_grid_engine():
    grid = GridState.empty(width=6, height=3)
    grid.entities["pc"] = Entity(id="pc", name="PC", x=0, y=1, attack_range=5)
    grid.entities["npc"] = Entity(id="npc", name="NPC", x=5, y=1)
    grid.cells[(3, 1)] = Cell(x=3, y=1, terrain="stone_wall", blocks_los=True)

    result = MapCalculator(grid).check_attack_vector("pc", "npc")

    assert result["ok"] is True
    assert result["can_attack"] is False
    assert result["reason"] == "line_of_sight_blocked"
    assert result["distance"] == 5
    assert result["range"] == 5
    assert result["los_clear"] is False
    assert result["blocked_by"][0]["terrain"] == "stone_wall"
    assert "calculation" not in result


def test_map_calculator_rejects_unsupported_non_strict_route():
    grid = GridState.empty(width=5, height=5)
    grid.entities["pc"] = Entity(id="pc", name="PC", x=1, y=1, move_points=6)
    route = MapCalculationRoute(
        operation="",
        map_type="overview_map",
        rule_scale="zone_band",
        strict=False,
        purpose="exploration",
        map_id="overview",
    )

    result = MapCalculator(grid, route).move_entity("pc", 2, 1)

    assert result["ok"] is False
    assert result["error_code"] == "unsupported_map_calculator_route"
    assert grid.entities["pc"].x == 1
    assert result["calculation"] == {
        "calculator": "none",
        "operation": "move_entity",
        "map_type": "overview_map",
        "rule_scale": "zone_band",
        "strict": False,
        "purpose": "exploration",
        "map_id": "overview",
    }

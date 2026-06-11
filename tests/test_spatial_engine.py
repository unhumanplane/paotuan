from astrbot_plugin_auto_trpg_dm.spatial.engine import SpatialEngine
from astrbot_plugin_auto_trpg_dm.spatial.grid import Cell, Entity, GridState


def test_move_entity_rejects_blocked_path():
    grid = GridState.empty(width=5, height=5)
    for y in range(5):
        grid.cells[(2, y)] = Cell(x=2, y=y, terrain="wall", blocks_move=True, blocks_los=True)
    grid.entities["pc"] = Entity(id="pc", name="PC", x=0, y=2, move_points=10)

    result = SpatialEngine(grid).move_entity("pc", 4, 2)

    assert result["ok"] is False
    assert result["error_code"] == "no_path_or_insufficient_move_points"
    assert grid.entities["pc"].x == 0
    assert grid.entities["pc"].y == 2


def test_move_entity_updates_position_when_reachable():
    grid = GridState.empty(width=5, height=5)
    grid.entities["pc"] = Entity(id="pc", name="PC", x=0, y=0, move_points=4)

    result = SpatialEngine(grid).move_entity("pc", 2, 2)

    assert result["ok"] is True
    assert result["cost"] == 4
    assert grid.entities["pc"].x == 2
    assert grid.entities["pc"].y == 2


def test_corpse_entity_does_not_block_movement_or_take_turn():
    grid = GridState.empty(width=5, height=5)
    grid.entities["pc"] = Entity(id="pc", name="PC", x=0, y=1, move_points=4)
    grid.entities["corpse"] = Entity(
        id="corpse",
        name="Fallen guard",
        x=1,
        y=1,
        blocks_move=True,
        tags={"status": "dead corpse"},
    )

    result = SpatialEngine(grid).move_entity("pc", 2, 1)
    corpse_move = SpatialEngine(grid).move_entity("corpse", 2, 2)

    assert result["ok"] is True
    assert result["path"] == [{"x": 0, "y": 1}, {"x": 1, "y": 1}, {"x": 2, "y": 1}]
    assert corpse_move["ok"] is False
    assert corpse_move["error_code"] == "entity_cannot_act"
    assert corpse_move["life_state"] == "corpse"


def test_attack_vector_rejects_incapacitated_target_as_normal_attack():
    grid = GridState.empty(width=5, height=5)
    grid.entities["pc"] = Entity(id="pc", name="PC", x=0, y=1, attack_range=5)
    grid.entities["corpse"] = Entity(id="corpse", name="Fallen guard", x=2, y=1, tags={"status": "阵亡尸体"})

    result = SpatialEngine(grid).check_attack_vector("pc", "corpse")

    assert result["ok"] is True
    assert result["can_attack"] is False
    assert result["reason"] == "target_incapacitated"
    assert result["target_life_state"] == "corpse"


def test_attack_vector_blocks_line_of_sight():
    grid = GridState.empty(width=6, height=3)
    grid.cells[(2, 1)] = Cell(x=2, y=1, terrain="stone_wall", blocks_los=True)
    grid.entities["pc"] = Entity(id="pc", name="PC", x=0, y=1, attack_range=10)
    grid.entities["npc"] = Entity(id="npc", name="NPC", x=5, y=1)

    result = SpatialEngine(grid).check_attack_vector("pc", "npc")

    assert result["ok"] is True
    assert result["can_attack"] is False
    assert result["reason"] == "line_of_sight_blocked"
    assert result["blocked_by"][0]["terrain"] == "stone_wall"


def test_attack_vector_allows_clear_line():
    grid = GridState.empty(width=6, height=3)
    grid.entities["pc"] = Entity(id="pc", name="PC", x=0, y=1, attack_range=10)
    grid.entities["npc"] = Entity(id="npc", name="NPC", x=5, y=1)

    result = SpatialEngine(grid).check_attack_vector("pc", "npc")

    assert result["ok"] is True
    assert result["can_attack"] is True
    assert result["reason"] == "ok"

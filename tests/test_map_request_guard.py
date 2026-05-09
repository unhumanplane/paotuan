from astrbot_plugin_auto_trpg_dm.core.map_request_guard import (
    DETERMINISTIC_MAP_RENDERER_TOOLS,
    build_map_request_guard,
    build_map_delivery_ack,
    build_missing_map_data_response,
    classify_map_renderer_results,
    completion_needs_map_delivery_ack,
    completion_looks_like_text_map,
    looks_text_only_map_request,
)


def test_visual_map_guard_requires_available_deterministic_renderer():
    guard = build_map_request_guard(
        "画一张当前战场站位图",
        available_tool_names=["render_strict_grid_svg", "move_entity"],
    )

    assert guard.visual_map_request is True
    assert guard.text_only_map_request is False
    assert guard.renderer_attempt_required is True
    assert guard.preferred_renderer_tools == ("render_strict_grid_svg",)


def test_visual_map_guard_honors_explicit_text_only_override():
    guard = build_map_request_guard(
        "先用 ASCII 文字地图画一下战场格子，不要生成图片",
        available_tool_names=DETERMINISTIC_MAP_RENDERER_TOOLS,
    )

    assert looks_text_only_map_request("用 text-only sketch 画一张地图")
    assert guard.visual_map_request is True
    assert guard.text_only_map_request is True
    assert guard.renderer_attempt_required is False
    assert guard.preferred_renderer_tools == ()


def test_text_map_detector_flags_grid_like_map_completion():
    completion = """
战场示意图：

+---+---+---+
| P | . | G |
+---+---+---+
| . | X | E |
+---+---+---+
""".strip()

    assert completion_looks_like_text_map(completion)


def test_text_map_detector_flags_emoji_tile_map_completion():
    completion = """
地图如下：
🧍⬜⬜🧱
⬜⬜👹⬜
⬜🚪⬜⬜
""".strip()

    assert completion_looks_like_text_map(completion)


def test_text_map_detector_avoids_common_non_map_outputs():
    assert not completion_looks_like_text_map("现在还不能生成可靠的可视化地图：当前缺少可渲染的结构化地图数据。")
    assert not completion_looks_like_text_map("你看到走廊向北延伸，左侧有一扇半开的门，远处传来脚步声。")
    assert not completion_looks_like_text_map(
        """
本轮检定摘要：
| 项目 | 结果 |
| 潜行 | 17 |
| 察觉 | 9 |
""".strip()
    )
    assert not completion_looks_like_text_map(
        """
玩家名单：
| 玩家 | 角色 |
| A | 战士 |
| B | 法师 |
""".strip()
    )


def test_classify_map_renderer_results_tracks_attempt_success_and_missing_data():
    summary = classify_map_renderer_results(
        [
            {"tool": "render_strict_grid_svg", "result": {"ok": False, "error": "strict_grid_not_found"}},
            {
                "tool": "render_overview_topology_svg",
                "result": {"ok": True, "render_type": "overview_topology_svg"},
            },
            {"tool": "generate_map_svg", "result": {"ok": True}},
        ]
    )

    assert summary.attempted is True
    assert summary.succeeded is True
    assert summary.missing_data is True
    assert summary.attempted_tools == ("render_strict_grid_svg", "render_overview_topology_svg")
    assert summary.legacy_attempted is True


def test_missing_map_data_response_is_structured_and_path_free():
    response = build_missing_map_data_response()

    assert "结构化地图数据" in response
    assert "C:/" not in response
    assert "D:/" not in response
    assert "svg" not in response.lower()


def test_generic_renderer_completion_needs_delivery_ack():
    assert completion_needs_map_delivery_ack("")
    assert completion_needs_map_delivery_ack("好的。")
    assert completion_needs_map_delivery_ack("地图已生成")
    assert not completion_needs_map_delivery_ack(build_map_delivery_ack())
    assert not completion_needs_map_delivery_ack("地图已生成，门厅和北侧楼梯都在附件里。")

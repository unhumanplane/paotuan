import json

from astrbot_plugin_auto_trpg_dm.core.prompt_projection import (
    project_ra_summary_for_dm_prompt,
    project_tool_results_for_dm_prompt,
)


def test_tool_results_projection_blocks_raw_hidden_and_backend_materials():
    projected = project_tool_results_for_dm_prompt(
        [
            {
                "tool": "generate_map_svg",
                "args": {
                    "title": "北门战场",
                    "prompt": "raw drawing prompt should not enter ordinary DM prompt",
                    "metadata_path": "D:/runtime/maps/map.svg.json",
                },
                "result": {
                    "ok": True,
                    "title": "北门战场",
                    "message": "SVG 地图已生成。",
                    "file_path": "D:/runtime/maps/map.svg",
                    "url": "https://example.invalid/map.svg",
                    "raw_svg": "<svg>secret</svg>",
                    "web_grounding": {"raw_content": "backend search payload"},
                    "rule_packages": {"dnd2024": {"raw_text": "full rule package"}},
                    "facts": [
                        {"id": "visible", "visibility": "dm", "text": "门口有倒塌马车。"},
                        {"id": "hidden-trigger", "visibility": "hidden", "text": "地砖下有机关。"},
                    ],
                },
            }
        ]
    )

    rendered = json.dumps(projected, ensure_ascii=False)

    assert projected[0]["tool"] == "generate_map_svg"
    assert projected[0]["args"] == {"title": "北门战场"}
    assert "SVG 地图已生成" in rendered
    assert "门口有倒塌马车" in rendered
    assert "hidden-trigger" not in rendered
    assert "D:/runtime" not in rendered
    assert "example.invalid" not in rendered
    assert "raw drawing prompt" not in rendered
    assert "backend search payload" not in rendered
    assert "full rule package" not in rendered


def test_strict_grid_renderer_tool_projection_keeps_metadata_without_paths_or_grid():
    projected = project_tool_results_for_dm_prompt(
        [
            {
                "tool": "render_strict_grid_svg",
                "args": {
                    "title": "北门战场",
                    "metadata_path": "D:/runtime/maps/strict.svg.json",
                },
                "result": {
                    "ok": True,
                    "map_id": "strict-local-map",
                    "title": "北门战场",
                    "file_path": "D:/runtime/maps/strict.svg",
                    "file_name": "strict.svg",
                    "svg_chars": 4096,
                    "send_to_chat": True,
                    "visual_only": True,
                    "render_ref": {
                        "type": "strict_grid_svg",
                        "title": "北门战场",
                        "name": "strict.svg",
                        "path": "D:/runtime/maps/strict.svg",
                        "visual_only": True,
                    },
                    "grid": {"width": 12, "height": 12, "entities": {"hidden": {"x": 9, "y": 9}}},
                    "raw_svg": "<svg>hidden</svg>",
                },
            }
        ]
    )

    rendered = json.dumps(projected, ensure_ascii=False)

    assert projected[0]["tool"] == "render_strict_grid_svg"
    assert projected[0]["args"] == {"title": "北门战场"}
    assert "strict_grid_svg" in rendered
    assert "strict.svg" in rendered
    assert "visual_only" in rendered
    assert "D:/runtime" not in rendered
    assert "hidden" not in rendered
    assert "raw_svg" not in rendered
    assert '"grid"' not in rendered


def test_overview_topology_render_projection_blocks_paths_svg_and_hidden_layout():
    projected = project_tool_results_for_dm_prompt(
        [
            {
                "tool": "render_overview_topology_svg",
                "args": {
                    "title": "北门概览",
                    "map_id": "overview-1",
                    "width": 900,
                    "height": 700,
                },
                "result": {
                    "ok": True,
                    "render_type": "overview_topology_svg",
                    "map_id": "overview-1",
                    "title": "北门概览",
                    "file_path": "D:/runtime/maps/overview.svg",
                    "file_name": "overview.svg",
                    "svg": "<svg>secret coordinates</svg>",
                    "raw_svg": "<svg>raw hidden</svg>",
                    "pending_output": {
                        "type": "svg_map",
                        "render_type": "overview_topology_svg",
                        "preferred_render_type": "overview_topology_svg",
                        "delivery_trigger": "overview_transition",
                        "delivery_reason": "eligible",
                        "cadence_key": "internal-cadence-key",
                        "title": "北门概览",
                        "name": "overview.svg",
                        "path": "D:/runtime/maps/overview.svg",
                        "visual_only": True,
                    },
                    "layout": {
                        "positions": {
                            "gate": {"x": 60, "y": 80},
                            "hidden-room": {"x": 900, "y": 900, "visibility": "hidden"},
                        }
                    },
                    "layout_updates": {
                        "cached": True,
                        "generated_node_ids": ["gate", "hidden-room"],
                        "positions": {"gate": {"x": 60, "y": 80}},
                    },
                    "facts": [
                        {"id": "visible-route", "visibility": "player", "text": "北门通往旧集市。"},
                        {"id": "hidden-route", "visibility": "hidden", "text": "密道通往地下室。"},
                    ],
                },
            }
        ]
    )

    rendered = json.dumps(projected, ensure_ascii=False)

    assert projected[0]["tool"] == "render_overview_topology_svg"
    assert projected[0]["args"] == {"title": "北门概览", "map_id": "overview-1", "width": 900, "height": 700}
    assert "overview_topology_svg" in rendered
    assert "北门通往旧集市" in rendered
    assert "D:/runtime" not in rendered
    assert "internal-cadence-key" not in rendered
    assert "cadence_key" not in rendered
    assert "<svg" not in rendered
    assert "secret coordinates" not in rendered
    assert "hidden-room" not in rendered
    assert "hidden-route" not in rendered


def test_ra_summary_projection_keeps_counts_not_rejected_values():
    projected = project_ra_summary_for_dm_prompt(
        {
            "cycle_id": 7,
            "summary": "队伍击退北门巡逻队。",
            "rules_triggered": ["attack"],
            "patch_candidates": {
                "character_status": [{"id": "pc-1", "hp": 7}],
            },
            "raw_ra_output": '{"secret": true}',
            "patch_validation": {
                "accepted": [
                    {
                        "category": "character_status",
                        "reason": "tool_backed_candidate_recorded",
                        "value": {"id": "pc-1", "hp": 7},
                    }
                ],
                "rejected": [
                    {
                        "category": "world_changes",
                        "reason": "missing_tool_backing",
                        "value": {
                            "text": "隐藏地图事实不应进入普通 DM prompt",
                            "visibility": "hidden",
                        },
                    }
                ],
            },
        }
    )

    rendered = json.dumps(projected, ensure_ascii=False)

    assert projected["cycle_id"] == 7
    assert projected["summary"] == "队伍击退北门巡逻队。"
    assert projected["patch_validation"]["accepted_count"] == 1
    assert projected["patch_validation"]["rejected_count"] == 1
    assert projected["patch_validation"]["rejected_categories"] == ["world_changes"]
    assert "raw_ra_output" not in rendered
    assert "patch_candidates" not in rendered
    assert "隐藏地图事实" not in rendered
    assert '"hp": 7' not in rendered

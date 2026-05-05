from astrbot_plugin_auto_trpg_dm.core.map_delivery_cadence import (
    MAP_DELIVERY_CADENCE_SCHEMA_VERSION,
    MAP_DELIVERY_CADENCE_SCENE_KEY,
    MAP_DELIVERY_TRIGGER_AREA_DISCOVERY,
    MAP_DELIVERY_TRIGGER_COMBAT_ROUND,
    MAP_DELIVERY_TRIGGER_OVERVIEW_TRANSITION,
    MAP_DELIVERY_TRIGGER_PLAYER_REQUEST,
    MAP_DELIVERY_TRIGGER_SPATIAL_CHANGE,
    MAP_DELIVERY_TRIGGER_STRICT_EXPLORATION_START,
    MAP_RENDER_LEGACY_LLM_SVG,
    MAP_RENDER_OVERVIEW_TOPOLOGY,
    MAP_RENDER_STRICT_GRID,
    MapDeliveryRequest,
    decide_map_delivery,
    default_map_delivery_cadence_state,
    enqueue_map_pending_output,
    filter_map_pending_outputs_for_delivery,
    get_map_delivery_cadence_state,
    record_map_delivery_sent,
    set_map_delivery_cadence_state,
)


def test_player_request_can_override_existing_cadence_record():
    state = default_map_delivery_cadence_state()
    request = MapDeliveryRequest(
        trigger=MAP_DELIVERY_TRIGGER_PLAYER_REQUEST,
        render_type=MAP_RENDER_OVERVIEW_TOPOLOGY,
        map_id="overview-1",
        map_revision="3",
        layout_revision="layout-1",
    )
    first = decide_map_delivery(state, request)
    state = record_map_delivery_sent(state, request, first)

    second = decide_map_delivery(state, request)

    assert first.should_send is True
    assert first.reason == "eligible"
    assert second.should_send is True
    assert second.reason == "player_request_override"
    assert second.duplicate is True
    assert second.render_type == MAP_RENDER_OVERVIEW_TOPOLOGY


def test_overview_transition_is_suppressed_after_same_revision_sent():
    state = default_map_delivery_cadence_state()
    request = MapDeliveryRequest(
        trigger=MAP_DELIVERY_TRIGGER_OVERVIEW_TRANSITION,
        map_id="overview-1",
        map_revision="transition-7",
        layout_revision="layout-2",
        trigger_id="scene:gate-to-market",
    )

    first = decide_map_delivery(state, request)
    state = record_map_delivery_sent(state, request, first)
    duplicate = decide_map_delivery(state, request)

    assert first.should_send is True
    assert first.preferred_render_type == MAP_RENDER_OVERVIEW_TOPOLOGY
    assert duplicate.should_send is False
    assert duplicate.reason == "duplicate_suppressed"
    assert duplicate.duplicate is True


def test_strict_exploration_start_defaults_to_strict_grid_renderer():
    request = MapDeliveryRequest(
        trigger=MAP_DELIVERY_TRIGGER_STRICT_EXPLORATION_START,
        map_id="strict-local-map",
        map_revision="11",
        trigger_id="strict-start:cellar",
    )

    decision = decide_map_delivery(default_map_delivery_cadence_state(), request)

    assert decision.should_send is True
    assert decision.render_type == MAP_RENDER_STRICT_GRID
    assert "strict-local-map" in decision.cadence_key


def test_area_discovery_uses_trigger_identity_for_deduplication():
    state = default_map_delivery_cadence_state()
    request = MapDeliveryRequest(
        trigger=MAP_DELIVERY_TRIGGER_AREA_DISCOVERY,
        map_id="overview-1",
        map_revision="4",
        trigger_id="area:old-market",
    )
    first = decide_map_delivery(state, request)
    state = record_map_delivery_sent(state, request, first)

    duplicate = decide_map_delivery(state, request)
    next_area = decide_map_delivery(
        state,
        MapDeliveryRequest(
            trigger=MAP_DELIVERY_TRIGGER_AREA_DISCOVERY,
            map_id="overview-1",
            map_revision="4",
            trigger_id="area:bell-tower",
        ),
    )

    assert duplicate.should_send is False
    assert duplicate.reason == "duplicate_suppressed"
    assert next_area.should_send is True
    assert next_area.cadence_key != first.cadence_key


def test_combat_round_sends_every_five_rounds_by_default():
    state = default_map_delivery_cadence_state()
    round_four = MapDeliveryRequest(
        trigger=MAP_DELIVERY_TRIGGER_COMBAT_ROUND,
        map_id="strict-local-map",
        map_revision="2",
        combat_id="combat-1",
        round_number=4,
    )
    round_five = MapDeliveryRequest(
        trigger=MAP_DELIVERY_TRIGGER_COMBAT_ROUND,
        map_id="strict-local-map",
        map_revision="2",
        combat_id="combat-1",
        round_number=5,
    )

    assert decide_map_delivery(state, round_four).reason == "combat_round_cadence_not_due"
    due = decide_map_delivery(state, round_five)
    state = record_map_delivery_sent(state, round_five, due)
    duplicate = decide_map_delivery(state, round_five)

    assert due.should_send is True
    assert due.render_type == MAP_RENDER_STRICT_GRID
    assert duplicate.should_send is False
    assert duplicate.reason == "duplicate_suppressed"


def test_ordinary_spatial_change_does_not_auto_send():
    decision = decide_map_delivery(
        default_map_delivery_cadence_state(),
        MapDeliveryRequest(
            trigger=MAP_DELIVERY_TRIGGER_SPATIAL_CHANGE,
            render_type=MAP_RENDER_STRICT_GRID,
            map_id="strict-local-map",
            map_revision="2",
        ),
    )

    assert decision.should_send is False
    assert decision.reason == "ordinary_spatial_change_not_auto_sent"


def test_renderer_unavailable_requires_explicit_legacy_fallback_permission():
    request = MapDeliveryRequest(
        trigger=MAP_DELIVERY_TRIGGER_OVERVIEW_TRANSITION,
        map_id="overview-1",
        map_revision="3",
        renderer_available=False,
    )
    denied = decide_map_delivery(default_map_delivery_cadence_state(), request)
    allowed = decide_map_delivery(
        default_map_delivery_cadence_state(),
        MapDeliveryRequest(
            trigger=MAP_DELIVERY_TRIGGER_OVERVIEW_TRANSITION,
            map_id="overview-1",
            map_revision="3",
            renderer_available=False,
            legacy_fallback_allowed=True,
        ),
    )

    assert denied.should_send is False
    assert denied.reason == "renderer_unavailable"
    assert allowed.should_send is True
    assert allowed.render_type == MAP_RENDER_LEGACY_LLM_SVG
    assert allowed.preferred_render_type == MAP_RENDER_OVERVIEW_TOPOLOGY
    assert allowed.legacy_fallback is True


def test_enqueue_pending_output_adds_delivery_metadata_and_marks_sent():
    scene = {}
    request = MapDeliveryRequest(
        trigger=MAP_DELIVERY_TRIGGER_OVERVIEW_TRANSITION,
        render_type=MAP_RENDER_OVERVIEW_TOPOLOGY,
        map_id="overview-1",
        map_revision="3",
        layout_revision="layout-1",
        trigger_id="scene:gate",
    )

    decision, state = enqueue_map_pending_output(
        scene,
        {"type": "svg_map", "title": "Gate", "path": "internal.svg", "visual_only": False},
        request,
    )

    assert decision.should_send is True
    assert len(state["sent"]) == 1
    pending = scene["_pending_outputs"][0]
    assert pending["visual_only"] is True
    assert pending["render_type"] == MAP_RENDER_OVERVIEW_TOPOLOGY
    assert pending["preferred_render_type"] == MAP_RENDER_OVERVIEW_TOPOLOGY
    assert pending["delivery_trigger"] == MAP_DELIVERY_TRIGGER_OVERVIEW_TRANSITION
    assert pending["delivery_reason"] == "eligible"
    assert pending["delivery_enqueued"] is True
    assert pending["map_id"] == "overview-1"
    assert pending["map_revision"] == "3"
    assert pending["layout_revision"] == "layout-1"
    assert pending["cadence_key"]


def test_filter_pending_outputs_records_sent_and_suppresses_duplicates():
    scene = {
        "_pending_outputs": [
            {
                "type": "svg_map",
                "render_type": MAP_RENDER_OVERVIEW_TOPOLOGY,
                "map_id": "overview-1",
                "map_revision": "3",
                "layout_revision": "layout-1",
                "delivery_trigger": MAP_DELIVERY_TRIGGER_OVERVIEW_TRANSITION,
                "trigger_id": "scene:gate",
            },
            {
                "type": "svg_map",
                "render_type": MAP_RENDER_OVERVIEW_TOPOLOGY,
                "map_id": "overview-1",
                "map_revision": "3",
                "layout_revision": "layout-1",
                "delivery_trigger": MAP_DELIVERY_TRIGGER_OVERVIEW_TRANSITION,
                "trigger_id": "scene:gate",
            },
        ]
    }

    kept, state, decisions = filter_map_pending_outputs_for_delivery(scene, scene["_pending_outputs"])

    assert len(kept) == 1
    assert kept[0]["delivery_reason"] == "eligible"
    assert decisions[0].should_send is True
    assert decisions[1].should_send is False
    assert decisions[1].reason == "duplicate_suppressed"
    assert len(state["sent"]) == 1
    assert scene[MAP_DELIVERY_CADENCE_SCENE_KEY] == state


def test_filter_queued_pending_output_keeps_already_enqueued_map_once():
    scene = {}
    request = MapDeliveryRequest(
        trigger=MAP_DELIVERY_TRIGGER_OVERVIEW_TRANSITION,
        render_type=MAP_RENDER_OVERVIEW_TOPOLOGY,
        map_id="overview-1",
        map_revision="3",
        layout_revision="layout-1",
        trigger_id="scene:gate",
    )
    enqueue_map_pending_output(
        scene,
        {"type": "svg_map", "title": "Gate", "path": "internal.svg"},
        request,
    )

    kept, state, decisions = filter_map_pending_outputs_for_delivery(scene, scene["_pending_outputs"])

    assert len(kept) == 1
    assert decisions[0].should_send is True
    assert decisions[0].reason == "eligible"
    assert len(state["sent"]) == 1


def test_filter_old_pending_svg_map_without_render_type_as_legacy_visual_only():
    scene = {
        "_pending_outputs": [
            {
                "type": "svg_map",
                "title": "Old generated map",
                "path": "internal/old.svg",
            }
        ]
    }

    kept, state, decisions = filter_map_pending_outputs_for_delivery(scene, scene["_pending_outputs"])

    assert len(kept) == 1
    output = kept[0]
    assert output["visual_only"] is True
    assert output["render_type"] == MAP_RENDER_LEGACY_LLM_SVG
    assert output["preferred_render_type"] == MAP_RENDER_LEGACY_LLM_SVG
    assert output["delivery_trigger"] == MAP_DELIVERY_TRIGGER_PLAYER_REQUEST
    assert output["delivery_enqueued"] is True
    assert decisions[0].should_send is True
    assert decisions[0].render_type == MAP_RENDER_LEGACY_LLM_SVG
    assert list(state["sent"].values())[0]["render_type"] == MAP_RENDER_LEGACY_LLM_SVG


def test_scene_cadence_state_normalizes_old_or_missing_values():
    scene = {}
    state = get_map_delivery_cadence_state(scene)
    assert state == {
        "schema_version": MAP_DELIVERY_CADENCE_SCHEMA_VERSION,
        "sent": {},
    }

    written = set_map_delivery_cadence_state(
        scene,
        {
            "schema_version": 999,
            "sent": {
                "key": {
                    "trigger": MAP_DELIVERY_TRIGGER_PLAYER_REQUEST,
                    "render_type": MAP_RENDER_STRICT_GRID,
                    "count": "2",
                },
                "bad": "not-a-record",
            },
        },
    )

    assert scene[MAP_DELIVERY_CADENCE_SCENE_KEY] == written
    assert written["schema_version"] == MAP_DELIVERY_CADENCE_SCHEMA_VERSION
    assert list(written["sent"]) == ["key"]
    assert written["sent"]["key"]["count"] == 2

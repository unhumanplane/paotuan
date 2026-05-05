import pytest

from astrbot_plugin_auto_trpg_dm.core.map_core import (
    MAP_AUTHORITY_RA_CANDIDATE,
    MAP_AUTHORITY_SPATIAL,
    MAP_SCHEMA_VERSION,
    MAP_EVENT_ADD_FACT,
    MAP_EVENT_CREATE_RECORD,
    MAP_TYPE_STRICT_LOCAL,
    MAP_VIEW_DIAGNOSTIC,
    MAP_VIEW_DM_NARRATION,
    MAP_VIEW_PLAYER,
    MAP_VIEW_RA_AUTHORITY,
    MAP_VISIBILITY_DM,
    MAP_VISIBILITY_HIDDEN,
    MAP_VISIBILITY_PLAYER,
    MAP_VISIBILITY_PUBLIC,
    add_map_fact,
    add_render_ref,
    create_map_record,
    default_map_store,
    get_map_record,
    load_active_strict_grid,
    migrate_legacy_battle_grid,
    normalize_map_store,
    project_active_map_record,
    project_map_store,
    save_active_strict_grid,
    update_map_record,
    validate_candidate_map_event,
)
from astrbot_plugin_auto_trpg_dm.core.models import GameSession


def test_game_session_loads_old_save_with_default_map_store():
    session = GameSession.from_dict({"session_id": "group", "battle": {"active": False}})

    assert session.maps == default_map_store()
    assert session.to_dict()["maps"]["schema_version"] == MAP_SCHEMA_VERSION


def test_map_store_normalization_drops_invalid_active_ids():
    store = normalize_map_store(
        {
            "schema_version": "1",
            "active_overview_map_id": "missing",
            "active_strict_map_id": "strict-1",
            "records": {
                "strict-1": {
                    "id": "strict-1",
                    "type": "strict",
                    "title": "Strict tactical map",
                    "visibility": "hidden",
                    "authority": "spatial",
                }
            },
            "archive_identity": {"campaign": "c1"},
        }
    )

    assert store["active_overview_map_id"] == ""
    assert store["active_strict_map_id"] == "strict-1"
    assert store["records"]["strict-1"]["type"] == MAP_TYPE_STRICT_LOCAL
    assert store["records"]["strict-1"]["visibility"] == MAP_VISIBILITY_HIDDEN
    assert store["records"]["strict-1"]["authority"] == MAP_AUTHORITY_SPATIAL


def test_map_helpers_create_update_and_read_records():
    store = default_map_store()

    record = create_map_record(
        store,
        "overview-1",
        title="Gatehouse overview",
        visibility=MAP_VISIBILITY_PLAYER,
        set_active=True,
    )
    updated = update_map_record(
        store,
        "overview-1",
        title="Gatehouse after alarm",
        authority=MAP_AUTHORITY_SPATIAL,
    )

    assert record["id"] == "overview-1"
    assert store["active_overview_map_id"] == "overview-1"
    assert updated["title"] == "Gatehouse after alarm"
    assert get_map_record(store, "overview-1")["authority"] == MAP_AUTHORITY_SPATIAL


def test_map_helpers_add_facts_and_render_refs_without_raw_mutation():
    store = default_map_store()
    create_map_record(store, "overview-1")

    fact = add_map_fact(
        store,
        "overview-1",
        fact_id="secret-door",
        kind="feature",
        text="A concealed door sits behind the old statue.",
        payload={"x": 4, "y": 2},
        visibility=MAP_VISIBILITY_HIDDEN,
        authority=MAP_AUTHORITY_RA_CANDIDATE,
        source="ra_candidate",
    )
    ref = add_render_ref(
        store,
        "overview-1",
        ref_type="svg_map",
        title="Gatehouse map",
        name="gatehouse.svg",
        path="/local/runtime/path/gatehouse.svg",
    )

    record = get_map_record(store, "overview-1")
    assert fact["visibility"] == MAP_VISIBILITY_HIDDEN
    assert record["facts"][0]["id"] == "secret-door"
    assert ref["visual_only"] is True
    assert record["render_refs"][0]["path"].endswith("gatehouse.svg")


def test_map_helpers_reject_duplicate_or_missing_records():
    store = default_map_store()
    create_map_record(store, "overview-1")

    with pytest.raises(ValueError, match="map_record_exists:overview-1"):
        create_map_record(store, "overview-1")
    with pytest.raises(ValueError, match="map_record_not_found:missing"):
        update_map_record(store, "missing", title="Nope")
    with pytest.raises(ValueError, match="map_record_not_found:missing"):
        add_map_fact(store, "missing", fact_id="f1", kind="feature")


def test_player_and_dm_projection_filter_hidden_facts_and_raw_render_paths():
    store = default_map_store()
    create_map_record(store, "overview-1", visibility=MAP_VISIBILITY_PLAYER, set_active=True)
    add_map_fact(
        store,
        "overview-1",
        fact_id="visible-door",
        kind="feature",
        text="The bronze door is visible from the courtyard.",
        visibility=MAP_VISIBILITY_PUBLIC,
    )
    add_map_fact(
        store,
        "overview-1",
        fact_id="secret-tunnel",
        kind="secret",
        text="A hidden tunnel starts under the statue.",
        visibility=MAP_VISIBILITY_HIDDEN,
    )
    add_render_ref(
        store,
        "overview-1",
        ref_type="svg_map",
        title="Overview",
        name="overview.svg",
        path="/local/runtime/maps/overview.svg",
        url="https://example.invalid/overview.svg",
    )

    player_view = project_map_store(store, MAP_VIEW_PLAYER)
    dm_view = project_map_store(store, MAP_VIEW_DM_NARRATION)

    player_record = player_view["records"]["overview-1"]
    dm_record = dm_view["records"]["overview-1"]
    assert [fact["id"] for fact in player_record["facts"]] == ["visible-door"]
    assert [fact["id"] for fact in dm_record["facts"]] == ["visible-door"]
    assert player_record["render_refs"][0] == {
        "type": "svg_map",
        "title": "Overview",
        "name": "overview.svg",
        "visual_only": True,
    }
    assert "path" not in str(player_view)
    assert "secret-tunnel" not in str(dm_view)


def test_ra_authority_projection_still_excludes_hidden_facts():
    store = default_map_store()
    create_map_record(store, "overview-1", visibility=MAP_VISIBILITY_DM, set_active=True)
    add_map_fact(
        store,
        "overview-1",
        fact_id="dm-visible-pressure",
        kind="pressure",
        text="The corridor is unstable.",
        visibility=MAP_VISIBILITY_DM,
    )
    add_map_fact(
        store,
        "overview-1",
        fact_id="hidden-trap-trigger",
        kind="trap",
        text="The trap trigger is beneath the third tile.",
        visibility=MAP_VISIBILITY_HIDDEN,
    )

    ra_record = project_active_map_record(store, MAP_VIEW_RA_AUTHORITY)

    assert ra_record["facts"][0]["id"] == "dm-visible-pressure"
    assert "hidden-trap-trigger" not in str(ra_record)


def test_diagnostic_projection_reports_counts_without_fact_payloads():
    store = default_map_store()
    create_map_record(store, "overview-1", visibility=MAP_VISIBILITY_DM, set_active=True)
    add_map_fact(
        store,
        "overview-1",
        fact_id="hidden-trap-trigger",
        kind="trap",
        payload={"x": 3, "y": 4},
        visibility=MAP_VISIBILITY_HIDDEN,
    )

    diagnostic = project_map_store(store, MAP_VIEW_DIAGNOSTIC)

    assert diagnostic["record_count"] == 1
    assert diagnostic["records"]["overview-1"]["fact_count"] == 1
    assert diagnostic["records"]["overview-1"]["hidden_fact_count"] == 1
    assert "hidden-trap-trigger" not in str(diagnostic)
    assert '"x": 3' not in str(diagnostic)


def test_candidate_map_event_validates_proposal_without_mutating_store():
    store = default_map_store()
    create_map_record(store, "overview-1", visibility=MAP_VISIBILITY_DM, set_active=True)
    before = normalize_map_store(store)

    validation = validate_candidate_map_event(
        store,
        {
            "event_type": MAP_EVENT_ADD_FACT,
            "map_id": "overview-1",
            "payload": {
                "fact_id": "loose-stone",
                "kind": "feature",
                "text": "A loose stone marks the old drain.",
                "visibility": MAP_VISIBILITY_DM,
            },
            "source": "ra",
            "confidence": 0.8,
        },
    )

    assert validation["ok"] is True
    assert validation["status"] == "candidate_valid"
    assert validation["event"]["payload"]["fact_id"] == "loose-stone"
    assert normalize_map_store(store) == before


def test_candidate_map_event_rejects_raw_patch_attempts():
    store = default_map_store()
    create_map_record(store, "overview-1")

    validation = validate_candidate_map_event(
        store,
        {
            "event_type": MAP_EVENT_ADD_FACT,
            "map_id": "overview-1",
            "patch": {"records": {"overview-1": {"facts": []}}},
            "payload": {"fact_id": "f1", "kind": "feature"},
        },
    )

    assert validation["ok"] is False
    assert validation["reason"] == "raw_patch_not_allowed"
    assert "patch" in validation["blocked_keys"]


def test_candidate_map_event_rejects_hidden_visibility_from_agents():
    store = default_map_store()
    create_map_record(store, "overview-1")

    validation = validate_candidate_map_event(
        store,
        {
            "event_type": MAP_EVENT_ADD_FACT,
            "map_id": "overview-1",
            "payload": {
                "fact_id": "hidden-trigger",
                "kind": "trap",
                "visibility": MAP_VISIBILITY_HIDDEN,
            },
        },
    )

    assert validation["ok"] is False
    assert validation["reason"] == "candidate_visibility_not_allowed"


def test_candidate_map_event_rejects_unknown_map_or_unsupported_event():
    store = default_map_store()

    unknown = validate_candidate_map_event(
        store,
        {
            "event_type": MAP_EVENT_ADD_FACT,
            "map_id": "missing",
            "payload": {"fact_id": "f1", "kind": "feature"},
        },
    )
    unsupported = validate_candidate_map_event(
        store,
        {
            "event_type": "direct_state_patch",
            "map_id": "missing",
            "payload": {},
        },
    )
    create_candidate = validate_candidate_map_event(
        store,
        {
            "event_type": MAP_EVENT_CREATE_RECORD,
            "map_id": "new-map",
            "payload": {"title": "New map"},
        },
    )

    assert unknown["reason"] == "unknown_map_id"
    assert unsupported["reason"] == "unsupported_event_type"
    assert create_candidate["ok"] is True


def test_create_map_record_sets_strict_local_map_active_slot():
    store = default_map_store()

    record = create_map_record(
        store,
        "combat-room",
        title="Combat room",
        map_type=MAP_TYPE_STRICT_LOCAL,
        authority=MAP_AUTHORITY_SPATIAL,
        set_active=True,
    )

    assert record["type"] == MAP_TYPE_STRICT_LOCAL
    assert store["active_strict_map_id"] == "combat-room"
    assert store["active_overview_map_id"] == ""


def test_load_active_strict_grid_prefers_map_store_over_legacy_battle_grid():
    store = default_map_store()
    strict_grid = {"width": 3, "height": 3, "cells": [], "entities": {"pc": {"x": 1, "y": 1}}}
    legacy_battle = {
        "active": True,
        "grid": {"width": 9, "height": 9, "cells": [], "entities": {"legacy": {"x": 8, "y": 8}}},
    }
    save_active_strict_grid(store, strict_grid, map_id="strict-room")

    loaded = load_active_strict_grid(store, legacy_battle)

    assert loaded["ok"] is True
    assert loaded["source"] == "map_store"
    assert loaded["map_id"] == "strict-room"
    assert loaded["grid"] == strict_grid


def test_load_active_strict_grid_falls_back_to_legacy_battle_grid():
    store = default_map_store()
    legacy_grid = {"width": 9, "height": 9, "cells": [], "entities": {"legacy": {"x": 8, "y": 8}}}

    loaded = load_active_strict_grid(store, {"active": True, "grid": legacy_grid})

    assert loaded["ok"] is True
    assert loaded["source"] == "legacy_battle_grid"
    assert loaded["grid"] == legacy_grid
    assert loaded["migration_required"] is True


def test_migrate_legacy_battle_grid_wraps_grid_with_auditable_metadata():
    store = default_map_store()
    legacy_grid = {"width": 5, "height": 5, "cells": [], "entities": {"pc": {"x": 0, "y": 0}}}

    migration = migrate_legacy_battle_grid(store, {"active": True, "grid": legacy_grid}, map_id="legacy-room")

    assert migration["ok"] is True
    assert migration["status"] == "legacy_battle_grid_migrated"
    assert migration["map_id"] == "legacy-room"
    assert store["active_strict_map_id"] == "legacy-room"
    record = get_map_record(store, "legacy-room")
    assert record["type"] == MAP_TYPE_STRICT_LOCAL
    assert record["grid"] == legacy_grid
    assert record["archive_identity"]["migration_source"] == "battle.grid"
    assert record["archive_identity"]["strict_grid_adapter_version"] == 1


def test_migrate_legacy_battle_grid_does_not_overwrite_map_store_authority():
    store = default_map_store()
    strict_grid = {"width": 3, "height": 3, "cells": [], "entities": {"pc": {"x": 1, "y": 1}}}
    legacy_grid = {"width": 9, "height": 9, "cells": [], "entities": {"legacy": {"x": 8, "y": 8}}}
    save_active_strict_grid(store, strict_grid, map_id="strict-room")

    migration = migrate_legacy_battle_grid(store, {"active": True, "grid": legacy_grid}, map_id="legacy-room")

    assert migration["ok"] is True
    assert migration["status"] == "strict_grid_already_authoritative"
    assert migration["migrated"] is False
    assert store["active_strict_map_id"] == "strict-room"
    assert get_map_record(store, "strict-room")["grid"] == strict_grid

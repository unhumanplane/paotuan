import pytest

from astrbot_plugin_auto_trpg_dm.core.map_core import (
    MAP_AUTHORITY_RA_CANDIDATE,
    MAP_AUTHORITY_SPATIAL,
    MAP_SCHEMA_VERSION,
    MAP_VISIBILITY_HIDDEN,
    MAP_VISIBILITY_PLAYER,
    add_map_fact,
    add_render_ref,
    create_map_record,
    default_map_store,
    get_map_record,
    normalize_map_store,
    update_map_record,
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

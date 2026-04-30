import asyncio

from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.rulebook_tools import RulebookTools


def test_query_core_rules_does_not_write_session_state(tmp_path):
    repository = JsonGameRepository(tmp_path)
    session = repository.load_session("group-1")
    session.world_tags["genre"] = "DND"
    session.world_tags["tone"] = "test"
    repository.save_session(session)
    before = repository.load_session("group-1").to_dict()

    result = asyncio.run(
        RulebookTools(repository, "group-1").query_core_rules(
            query="施法被打断了吗",
            limit=3,
            max_chars=1200,
        )
    )

    after = repository.load_session("group-1").to_dict()
    assert result["ok"] is True
    assert result["available"] is True
    assert result["matches"]
    assert before == after

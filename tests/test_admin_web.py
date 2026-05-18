from __future__ import annotations

import asyncio

from astrbot_plugin_auto_trpg_dm.core.admin_web import AutoTrpgAdminWeb
from astrbot_plugin_auto_trpg_dm.core.models import Character, GameSession, TagValue
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository


class DummyContext:
    def __init__(self):
        self.routes = []

    def register_web_api(self, route, handler, methods, description):
        self.routes.append(
            {
                "route": route,
                "handler": handler,
                "methods": methods,
                "description": description,
            }
        )


def _unwrap(response):
    if isinstance(response, tuple):
        return response[0]
    return response


def test_admin_web_registers_read_only_routes(tmp_path):
    web = AutoTrpgAdminWeb(JsonGameRepository(tmp_path))
    context = DummyContext()

    count = web.register_routes(context, route_prefixes=("/auto_trpg_dm",))

    assert count == 5
    assert [item["route"] for item in context.routes] == [
        "/auto_trpg_dm/dm/web/status",
        "/auto_trpg_dm/dm/web/sessions",
        "/auto_trpg_dm/dm/web/session/snapshot",
        "/auto_trpg_dm/dm/web/session/audit",
        "/auto_trpg_dm/dm/web/session/backups",
    ]
    assert all(item["methods"] == ["GET"] for item in context.routes)


def test_admin_web_installs_static_dashboard_fallback(tmp_path):
    web = AutoTrpgAdminWeb(JsonGameRepository(tmp_path / "plugin-data"))

    result = web.install_static_dashboard(data_root=tmp_path)

    assert result["installed"] is True
    assert result["url"] == "/auto-trpg-dm-dashboard/index.html"
    dashboard_dir = tmp_path / "dist" / "auto-trpg-dm-dashboard"
    assert sorted(item.name for item in dashboard_dir.iterdir()) == ["app.js", "index.html", "style.css"]
    assert "fetchPluginApi" in (dashboard_dir / "app.js").read_text(encoding="utf-8")
    assert "/api/plug/auto_trpg_dm/" in (dashboard_dir / "app.js").read_text(encoding="utf-8")
    assert 'sessionEndpoint("snapshot", sessionKey)' in (dashboard_dir / "app.js").read_text(encoding="utf-8")


def test_admin_web_projects_session_snapshot_without_hidden_scene_or_paths(tmp_path):
    repo = JsonGameRepository(tmp_path)
    session = GameSession.new("group:one")
    session.title = "雾港回声"
    session.scene.update(
        {
            "summary": "码头暴雨仍未停。",
            "current_objective": "调查失踪渔船",
            "open_hooks": [{"text": "灯塔重新亮起", "status": "open"}],
            "hidden_truth": "灯塔看守是幕后黑手",
            "secret_clues": [{"text": "密室钥匙", "visibility": "dm"}],
        }
    )
    session.world_tags.update(
        {
            "genre": "悬疑",
            "api_key": "should-not-leak",
            "hidden_motive": "should-not-leak",
        }
    )
    session.characters["pc"] = Character(
        id="pc",
        name="兰",
        summary="谨慎的调查员",
        tags=[TagValue(layer="relations", key="npc", value={"trust": "low", "hidden_motive": "secret"})],
    )
    repo.save_session(session)

    web = AutoTrpgAdminWeb(repo)
    response = _unwrap(asyncio.run(web.get_session_snapshot(session_key="group_one")))

    assert response["status"] == "ok"
    data = response["data"]
    assert data["title"] == "雾港回声"
    assert data["visible_scene"]["current_objective"] == "调查失踪渔船"
    assert "hidden_truth" not in data["visible_scene"]
    assert "secret_clues" not in data["visible_scene"]
    assert data["visible_world_tags"]["genre"] == "悬疑"
    assert "api_key" not in data["visible_world_tags"]
    assert "hidden_motive" not in data["visible_world_tags"]
    assert "灯塔看守" not in str(data)
    assert "should-not-leak" not in str(data)


def test_admin_web_lists_audit_and_backups_as_summaries(tmp_path):
    repo = JsonGameRepository(tmp_path)
    session = GameSession.new("group")
    session.title = "旧剧院失踪案"
    repo.save_session(session)
    backup_path = repo.backup_session("group", reason="manual_backup:查看前")
    repo.append_audit(
        "group",
        {
            "type": "tool",
            "tool": "update_scene",
            "input": {"raw_text": "should-not-leak", "file_path": "F:/secret.json"},
            "result": {"ok": True, "message": "已更新场景。"},
        },
    )

    assert backup_path is not None
    web = AutoTrpgAdminWeb(repo)

    audit_response = _unwrap(asyncio.run(web.get_session_audit(session_key="group")))
    backup_response = _unwrap(asyncio.run(web.get_session_backups(session_key="group")))

    assert audit_response["status"] == "ok"
    assert audit_response["data"][0]["tool"] == "update_scene"
    assert audit_response["data"][0]["message"] == "已更新场景。"
    assert "should-not-leak" not in str(audit_response)
    assert "F:/secret.json" not in str(audit_response)
    assert backup_response["status"] == "ok"
    assert backup_response["data"][0]["name"] == backup_path.name
    assert "path" not in backup_response["data"][0]


def test_admin_web_derives_display_title_for_untitled_sessions(tmp_path):
    repo = JsonGameRepository(tmp_path)
    session = GameSession.new("group")
    session.scene["current_objective"] = "找到失联侦察队的记录核心。"
    repo.save_session(session)

    web = AutoTrpgAdminWeb(repo)
    sessions_response = _unwrap(asyncio.run(web.get_sessions()))
    snapshot_response = _unwrap(asyncio.run(web.get_session_snapshot(session_key="group")))

    assert sessions_response["status"] == "ok"
    assert sessions_response["data"][0]["title"] == "失联侦察队的记录核心"
    assert sessions_response["data"][0]["stored_title"] == "未命名团"
    assert snapshot_response["data"]["title"] == "失联侦察队的记录核心"
    assert snapshot_response["data"]["stored_title"] == "未命名团"

from __future__ import annotations

import asyncio
import base64
import json
import urllib.error
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import astrbot_plugin_auto_trpg_dm.core.ambient_image as ambient_image_module
from astrbot_plugin_auto_trpg_dm.core.ambient_image import (
    AmbientImageConfig,
    AmbientImageProvider,
    parse_ambient_image_response,
)
from astrbot_plugin_auto_trpg_dm.core.models import GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.ambient_image_tools import (
    AmbientImageTools,
    _ambient_style_seed,
    ambient_image_gate,
    build_ambient_image_prompt_request,
    compose_ambient_image_prompt,
    explicit_ambient_image_trigger,
    render_ambient_image_prompt_template,
    should_offer_ambient_image,
    update_ambient_image_activity_state,
)


def _test_runtime_dir(name: str) -> Path:
    base = Path(__file__).parent / "_tmp_runtime"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{name}_{uuid.uuid4().hex}"
    path.mkdir()
    return path


def _public_dns_resolver(host: str, port: int) -> list[str]:
    return ["8.8.8.8"]


def _add_recent_player_messages(
    session: GameSession,
    *,
    count: int = 10,
    players: tuple[str, ...] = ("player-a", "player-b"),
    minutes_ago: int = 5,
) -> None:
    created_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    session.scene["ambient_image_recent_player_messages"] = [
        {
            "created_at": created_at,
            "player_id": players[index % len(players)],
            "message": f"玩家 {index} 描述了一个具体行动",
        }
        for index in range(count)
    ]


def test_ambient_image_disabled_skips_provider():
    provider = AmbientImageProvider(AmbientImageConfig(enabled=False), environ={})

    result = asyncio.run(provider.generate("城堡夜色"))

    assert result == {"ok": True, "available": False, "reason": "ambient_image_disabled"}


def test_ambient_image_missing_api_key_degrades_without_call():
    calls = []
    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, api_key_env="PACKY_KEY"),
        environ={},
        http_post=lambda *args: calls.append(args),
    )

    result = asyncio.run(provider.generate("城堡夜色"))

    assert result["ok"] is False
    assert result["error"] == "ambient_image_api_key_missing"
    assert calls == []


def test_ambient_image_direct_config_api_key_avoids_env_requirement():
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured.update({"headers": headers})
        return 200, {"content-type": "application/json"}, json.dumps({"data": [{"b64_json": base64.b64encode(b"png").decode()}]}).encode()

    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, api_key="direct-secret", response_format="b64_json"),
        environ={},
        http_post=fake_post,
    )

    result = asyncio.run(provider.generate("城堡夜色"))

    assert result["ok"] is True
    assert captured["headers"]["Authorization"] == "Bearer direct-secret"


def test_ambient_image_legacy_env_field_accepts_pasted_key():
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured.update({"headers": headers})
        return 200, {"content-type": "application/json"}, json.dumps({"data": [{"b64_json": base64.b64encode(b"png").decode()}]}).encode()

    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, api_key_env="sk-pasted-secret", response_format="b64_json"),
        environ={},
        http_post=fake_post,
    )

    result = asyncio.run(provider.generate("城堡夜色"))

    assert result["ok"] is True
    assert captured["headers"]["Authorization"] == "Bearer sk-pasted-secret"


def test_images_api_payload_defaults_to_single_medium_quality_1_5k_and_parses_url():
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured.update({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        return 200, {"content-type": "application/json"}, json.dumps({"data": [{"url": "https://cdn.example/img.png"}]}).encode()

    def fake_get(url, headers, timeout):
        assert url == "https://cdn.example/img.png"
        captured["download_headers"] = headers
        return 200, {"content-type": "image/png"}, b"png-bytes"

    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, api_key_env="PACKY_KEY"),
        environ={"PACKY_KEY": "secret"},
        http_post=fake_post,
        http_get=fake_get,
        dns_resolver=_public_dns_resolver,
    )

    result = asyncio.run(provider.generate("古堡门廊"))

    assert result["ok"] is True
    assert captured["url"] == "https://www.packyapi.com/v1/images/generations"
    assert captured["payload"]["model"] == "gpt-image-2"
    assert captured["payload"]["n"] == 1
    assert captured["payload"]["size"] == "1536x1024"
    assert captured["payload"]["quality"] == "medium"
    assert "Mozilla/5.0" in captured["headers"]["User-Agent"]
    assert "Mozilla/5.0" in captured["download_headers"]["User-Agent"]
    assert captured["timeout"] == 120
    assert result["image_bytes"] == b"png-bytes"
    assert result["extension"] == "png"


def test_ambient_image_custom_user_agent_is_used_for_provider_request():
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured.update({"headers": headers})
        return 200, {"content-type": "application/json"}, json.dumps({"data": [{"b64_json": base64.b64encode(b"png").decode()}]}).encode()

    provider = AmbientImageProvider(
        AmbientImageConfig(
            enabled=True,
            api_key_env="PACKY_KEY",
            response_format="b64_json",
            user_agent="CustomAmbientClient/1.0",
        ),
        environ={"PACKY_KEY": "secret"},
        http_post=fake_post,
    )

    result = asyncio.run(provider.generate("城堡夜色"))

    assert result["ok"] is True
    assert captured["headers"]["User-Agent"] == "CustomAmbientClient/1.0"


def test_images_api_parses_b64_json():
    image_b64 = base64.b64encode(b"png-bytes").decode()

    def fake_post(url, headers, payload, timeout):
        return 200, {"content-type": "application/json"}, json.dumps({"data": [{"b64_json": image_b64}]}).encode()

    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, response_format="b64_json", api_key_env="PACKY_KEY"),
        environ={"PACKY_KEY": "secret"},
        http_post=fake_post,
    )

    result = asyncio.run(provider.generate("林中废墟"))

    assert result["ok"] is True
    assert result["image_bytes"] == b"png-bytes"
    assert result["source"] == "b64_json"


def test_chat_completions_extracts_markdown_image_url():
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured.update({"url": url, "payload": payload})
        body = {
            "choices": [
                {
                    "message": {
                        "content": "图像生成完成\n![image](https://cdn.example/scene.webp)"
                    }
                }
            ]
        }
        return 200, {"content-type": "application/json"}, json.dumps(body).encode()

    def fake_get(url, headers, timeout):
        return 200, {"content-type": "image/webp"}, b"webp-bytes"

    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, api_mode="chat_completions", api_key_env="PACKY_KEY"),
        environ={"PACKY_KEY": "secret"},
        http_post=fake_post,
        http_get=fake_get,
        dns_resolver=_public_dns_resolver,
    )

    result = asyncio.run(provider.generate("雨夜码头"))

    assert result["ok"] is True
    assert captured["url"] == "https://www.packyapi.com/v1/chat/completions"
    assert captured["payload"]["messages"][0]["content"] == "雨夜码头"
    assert result["extension"] == "webp"


def test_parse_chat_completion_url_even_when_body_has_error_text():
    body = "client error but image is here ![image](https://cdn.example/fallback.png)"

    result = parse_ambient_image_response(body, api_mode="chat_completions")

    assert result == {"ok": True, "url": "https://cdn.example/fallback.png"}


def test_image_url_blocks_loopback_before_download():
    calls = []

    def fake_post(url, headers, payload, timeout):
        body = {"data": [{"url": "https://127.0.0.1/image.png"}]}
        return 200, {"content-type": "application/json"}, json.dumps(body).encode()

    def fake_get(url, headers, timeout):
        calls.append(url)
        raise AssertionError("loopback URL should be blocked before download")

    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, api_key_env="PACKY_KEY"),
        environ={"PACKY_KEY": "secret"},
        http_post=fake_post,
        http_get=fake_get,
    )

    result = asyncio.run(provider.generate("本机 URL"))

    assert result["ok"] is False
    assert result["error"] == "ambient_image_url_blocked"
    assert calls == []


def test_image_url_blocks_private_dns_resolution_before_download():
    calls = []

    def fake_post(url, headers, payload, timeout):
        body = {"data": [{"url": "https://cdn.example/image.png"}]}
        return 200, {"content-type": "application/json"}, json.dumps(body).encode()

    def fake_get(url, headers, timeout):
        calls.append(url)
        raise AssertionError("private DNS result should be blocked before download")

    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, api_key_env="PACKY_KEY"),
        environ={"PACKY_KEY": "secret"},
        http_post=fake_post,
        http_get=fake_get,
        dns_resolver=lambda host, port: ["10.0.0.5"],
    )

    result = asyncio.run(provider.generate("私网 DNS"))

    assert result["ok"] is False
    assert result["error"] == "ambient_image_url_blocked"
    assert result["reason"] == "resolved_ip_blocked"
    assert calls == []


def test_image_download_blocks_private_redirect_before_follow(monkeypatch):
    calls = []

    def fake_post(url, headers, payload, timeout):
        body = {"data": [{"url": "https://cdn.example/image.png"}]}
        return 200, {"content-type": "application/json"}, json.dumps(body).encode()

    class RedirectOpener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": "http://127.0.0.1/private.png"},
                None,
            )

    monkeypatch.setattr(ambient_image_module, "_NO_REDIRECT_OPENER", RedirectOpener())
    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, api_key_env="PACKY_KEY"),
        environ={"PACKY_KEY": "secret"},
        http_post=fake_post,
        dns_resolver=_public_dns_resolver,
    )

    result = asyncio.run(provider.generate("跳转到本机"))

    assert result["ok"] is False
    assert result["error"] == "ambient_image_url_blocked"
    assert result["url"] == "http://127.0.0.1/private.png"
    assert calls == ["https://cdn.example/image.png"]


def test_image_download_rejects_content_length_over_limit(monkeypatch):
    monkeypatch.setattr(ambient_image_module, "MAX_AMBIENT_IMAGE_BYTES", 4)

    def fake_post(url, headers, payload, timeout):
        body = {"data": [{"url": "https://cdn.example/image.png"}]}
        return 200, {"content-type": "application/json"}, json.dumps(body).encode()

    def fake_get(url, headers, timeout):
        return 200, {"content-type": "image/png", "content-length": "5"}, b"tiny"

    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, api_key_env="PACKY_KEY"),
        environ={"PACKY_KEY": "secret"},
        http_post=fake_post,
        http_get=fake_get,
        dns_resolver=_public_dns_resolver,
    )

    result = asyncio.run(provider.generate("超大 content length"))

    assert result["ok"] is False
    assert result["error"] == "ambient_image_too_large"
    assert result["source"] == "download"
    assert result["content_length"] == 5


def test_image_download_rejects_actual_bytes_over_limit(monkeypatch):
    monkeypatch.setattr(ambient_image_module, "MAX_AMBIENT_IMAGE_BYTES", 4)

    def fake_post(url, headers, payload, timeout):
        body = {"data": [{"url": "https://cdn.example/image.png"}]}
        return 200, {"content-type": "application/json"}, json.dumps(body).encode()

    def fake_get(url, headers, timeout):
        return 200, {"content-type": "image/png"}, b"12345"

    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, api_key_env="PACKY_KEY"),
        environ={"PACKY_KEY": "secret"},
        http_post=fake_post,
        http_get=fake_get,
        dns_resolver=_public_dns_resolver,
    )

    result = asyncio.run(provider.generate("超大实际内容"))

    assert result["ok"] is False
    assert result["error"] == "ambient_image_too_large"
    assert result["actual_bytes"] == 5


def test_image_download_rejects_non_image_content_type():
    def fake_post(url, headers, payload, timeout):
        body = {"data": [{"url": "https://cdn.example/image.png"}]}
        return 200, {"content-type": "application/json"}, json.dumps(body).encode()

    def fake_get(url, headers, timeout):
        return 200, {"content-type": "text/html"}, b"<html></html>"

    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, api_key_env="PACKY_KEY"),
        environ={"PACKY_KEY": "secret"},
        http_post=fake_post,
        http_get=fake_get,
        dns_resolver=_public_dns_resolver,
    )

    result = asyncio.run(provider.generate("非图片内容"))

    assert result["ok"] is False
    assert result["error"] == "ambient_image_content_type_invalid"
    assert result["content_type"] == "text/html"


def test_b64_json_rejects_payload_over_limit(monkeypatch):
    monkeypatch.setattr(ambient_image_module, "MAX_AMBIENT_IMAGE_BYTES", 4)
    image_b64 = base64.b64encode(b"12345").decode()

    def fake_post(url, headers, payload, timeout):
        return 200, {"content-type": "application/json"}, json.dumps({"data": [{"b64_json": image_b64}]}).encode()

    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, response_format="b64_json", api_key_env="PACKY_KEY"),
        environ={"PACKY_KEY": "secret"},
        http_post=fake_post,
    )

    result = asyncio.run(provider.generate("超大 base64"))

    assert result["ok"] is False
    assert result["error"] == "ambient_image_too_large"
    assert result["source"] == "b64_json"


def test_ambient_image_gate_blocks_combat_before_prompt_or_api():
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.battle = {"active": True}

    result = ambient_image_gate(session, AmbientImageConfig(enabled=True))

    assert result["reason"] == "ambient_image_combat_active"


def test_ambient_image_warmup_requires_frequent_interaction_plus_five_minutes():
    session = GameSession.new("group")
    config = AmbientImageConfig(enabled=True)
    for _ in range(11):
        update_ambient_image_activity_state(session)

    waiting = ambient_image_gate(session, config)
    session.scene["ambient_image_state"]["warmup_started_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=6)
    ).isoformat()
    _add_recent_player_messages(session)
    ready = ambient_image_gate(session, config)

    assert waiting["reason"] == "ambient_image_warmup_wait"
    assert ready["ok"] is True


def test_opening_trigger_is_explicit_capability_not_auto_enabled():
    session = GameSession.new("group")
    session.scene["summary"] = "黑塔城的第一夜，玩家刚刚抵达。"
    session.scene["_recent_narrative_events"] = [{"at": "now", "message": "开场", "outcome": "抵达黑塔城"}]
    config = AmbientImageConfig(enabled=True)

    automatic = ambient_image_gate(session, config)
    explicit = explicit_ambient_image_trigger(session, config, "opening")

    assert automatic["reason"] == "ambient_image_warmup_wait"
    assert explicit["ok"] is True
    assert explicit["trigger"] == "opening"


def test_manual_trigger_bypasses_automatic_warmup_but_not_combat():
    session = GameSession.new("group")
    session.scene["summary"] = "黑塔城的雾夜调查仍在继续。"
    config = AmbientImageConfig(enabled=True)

    automatic = ambient_image_gate(session, config)
    manual = ambient_image_gate(session, config, trigger_override="manual")
    session.mode = GameMode.TACTICAL
    session.battle = {"active": True}
    combat = ambient_image_gate(session, config, trigger_override="manual")

    assert automatic["reason"] == "ambient_image_warmup_wait"
    assert manual["ok"] is True
    assert manual["trigger"] == "manual"
    assert combat["reason"] == "ambient_image_combat_active"


def test_pause_and_resume_cooldowns_are_independent():
    session = GameSession.new("group")
    session.scene["summary"] = "调查暂时告一段落。"
    session.scene["_dm_paused"] = True
    session.scene["_dm_paused_at"] = datetime.now(timezone.utc).isoformat()
    session.scene["ambient_image_state"] = {
        "last_resume_generated_at": datetime.now(timezone.utc).isoformat()
    }
    config = AmbientImageConfig(enabled=True)

    pause_after_recent_resume = ambient_image_gate(session, config)
    session.scene["ambient_image_state"] = {
        "last_pause_generated_at": datetime.now(timezone.utc).isoformat()
    }
    pause_after_recent_pause = ambient_image_gate(session, config)

    session.scene["_dm_paused"] = False
    session.scene["_dm_resumed_at"] = datetime.now(timezone.utc).isoformat()
    session.scene["ambient_image_state"] = {
        "last_pause_generated_at": datetime.now(timezone.utc).isoformat()
    }
    resume_after_recent_pause = ambient_image_gate(session, config)
    session.scene["ambient_image_state"] = {
        "last_resume_generated_at": datetime.now(timezone.utc).isoformat()
    }
    resume_after_recent_resume = ambient_image_gate(session, config)

    assert pause_after_recent_resume["trigger"] == "pause_resume"
    assert pause_after_recent_resume["pause_resume_kind"] == "pause"
    assert pause_after_recent_pause["reason"] == "ambient_image_pause_resume_cooldown"
    assert pause_after_recent_pause["pause_resume_kind"] == "pause"
    assert resume_after_recent_pause["trigger"] == "pause_resume"
    assert resume_after_recent_pause["pause_resume_kind"] == "resume"
    assert resume_after_recent_resume["reason"] == "ambient_image_pause_resume_cooldown"
    assert resume_after_recent_resume["pause_resume_kind"] == "resume"


def test_pause_resume_trigger_bypasses_warmup_and_setup_filter():
    session = GameSession.new("group")
    session.scene["summary"] = "调查暂时告一段落。"
    session.scene["_dm_paused"] = False
    session.scene["_dm_resumed_at"] = datetime.now(timezone.utc).isoformat()
    session.scene["ambient_image_state"] = {
        "last_resume_generated_at": (datetime.now(timezone.utc) - timedelta(minutes=130)).isoformat()
    }
    config = AmbientImageConfig(enabled=True)

    result = ambient_image_gate(session, config)
    offered = should_offer_ambient_image(
        session,
        config,
        GameMode.NARRATIVE,
        player_message="恢复",
    )

    assert result["ok"] is True
    assert result["trigger"] == "pause_resume"
    assert offered is True


def test_interval_trigger_skips_when_activity_window_has_too_few_messages():
    session = GameSession.new("group")
    session.scene["summary"] = "黑塔城的街巷仍被浓雾覆盖。"
    session.scene["ambient_image_state"] = {
        "warmup_started_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
        "interaction_count": 12,
    }
    session.scene["_recent_narrative_events"] = [
        {"at": str(index), "message": f"行动 {index}", "outcome": "剧情推进"}
        for index in range(12)
    ]
    _add_recent_player_messages(session, count=9, players=("player-a", "player-b"))
    config = AmbientImageConfig(enabled=True)

    result = ambient_image_gate(session, config)

    assert result["reason"] == "ambient_image_activity_wait"
    assert result["activity_message_count"] == 9
    assert result["activity_min_messages"] == 10


def test_interval_trigger_skips_when_activity_window_has_one_speaker():
    session = GameSession.new("group")
    session.scene["summary"] = "黑塔城的街巷仍被浓雾覆盖。"
    session.scene["ambient_image_state"] = {
        "warmup_started_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
        "interaction_count": 12,
    }
    session.scene["_recent_narrative_events"] = [
        {"at": str(index), "message": f"行动 {index}", "outcome": "剧情推进"}
        for index in range(12)
    ]
    _add_recent_player_messages(session, count=10, players=("player-a",))
    config = AmbientImageConfig(enabled=True)

    result = ambient_image_gate(session, config)

    assert result["reason"] == "ambient_image_activity_wait"
    assert result["activity_player_count"] == 1
    assert result["activity_min_players"] == 2


def test_active_window_keeps_interval_trigger_eligible():
    session = GameSession.new("group")
    session.scene["summary"] = "黑塔城的街巷仍被浓雾覆盖。"
    session.scene["ambient_image_state"] = {
        "warmup_started_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
        "interaction_count": 12,
    }
    session.scene["_recent_narrative_events"] = [
        {"at": str(index), "message": f"行动 {index}", "outcome": "剧情推进"}
        for index in range(12)
    ]
    _add_recent_player_messages(session, count=10, players=("player-a", "player-b"))
    config = AmbientImageConfig(enabled=True)

    result = ambient_image_gate(session, config)

    assert result["ok"] is True
    assert result["trigger"] == "interval"


def test_pause_resume_trigger_bypasses_activity_gate():
    session = GameSession.new("group")
    session.scene["summary"] = "调查暂时告一段落。"
    session.scene["_dm_paused"] = False
    session.scene["_dm_resumed_at"] = datetime.now(timezone.utc).isoformat()
    session.scene["ambient_image_state"] = {
        "last_resume_generated_at": (datetime.now(timezone.utc) - timedelta(minutes=130)).isoformat(),
    }
    config = AmbientImageConfig(enabled=True)

    result = ambient_image_gate(session, config)

    assert result["ok"] is True
    assert result["trigger"] == "pause_resume"


def test_narrative_continue_text_does_not_trigger_pause_resume_image():
    session = GameSession.new("group")
    session.scene["summary"] = "黑塔城的雾夜调查仍在继续。"
    session.scene["ambient_image_state"] = {
        "warmup_started_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
        "interaction_count": 12,
        "last_turn_index": 10,
        "last_generated_at": datetime.now(timezone.utc).isoformat(),
    }
    session.scene["_recent_narrative_events"] = [
        {"at": str(index), "message": f"行动 {index}", "outcome": "剧情推进"}
        for index in range(10)
    ]
    session.scene["_recent_narrative_events"].append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "message": "我继续沿着雾里的脚印调查。",
            "outcome": "队伍继续调查，街巷尽头出现蓝灯。",
        }
    )
    _add_recent_player_messages(session, count=10, players=("player-a", "player-b"))
    config = AmbientImageConfig(enabled=True)

    result = ambient_image_gate(session, config)

    assert result["reason"] == "ambient_image_frequency_wait"
    assert result["turns_elapsed"] == 1


def test_direct_user_image_request_does_not_trigger_ambient_image():
    session = GameSession.new("group")
    session.scene["summary"] = "雾气笼罩黑塔城，城门前响起钟声。"
    session.scene["ambient_image_state"] = {
        "warmup_started_at": (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
        "interaction_count": 12,
    }
    _add_recent_player_messages(session)
    session.scene["_recent_narrative_events"] = [
        {"at": str(index), "message": f"行动 {index}", "outcome": "剧情推进"}
        for index in range(12)
    ]
    config = AmbientImageConfig(enabled=True)

    assert ambient_image_gate(session, config)["ok"] is True
    assert should_offer_ambient_image(
        session,
        config,
        GameMode.NARRATIVE,
        player_message="给这个 NPC 生成一张图片",
    ) is False


def test_low_frequency_requires_thirty_turns_or_forty_minutes():
    session = GameSession.new("group")
    session.scene["summary"] = "雾气笼罩黑塔城，城门前响起钟声。"
    session.scene["ambient_image_state"] = {
        "warmup_started_at": (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
        "interaction_count": 12,
        "last_turn_index": 1,
        "last_generated_at": datetime.now(timezone.utc).isoformat(),
    }
    session.scene["_recent_narrative_events"] = [
        {"at": str(index), "message": f"行动 {index}", "outcome": "剧情推进"}
        for index in range(30)
    ]
    _add_recent_player_messages(session, count=10, players=("player-a", "player-b"))
    config = AmbientImageConfig(enabled=True, frequency="low")

    waiting = ambient_image_gate(session, config)
    session.scene["_recent_narrative_events"].append(
        {"at": "30", "message": "查看钟楼", "outcome": "剧情推进到钟楼"}
    )
    by_turns = ambient_image_gate(session, config)
    session.scene["_recent_narrative_events"] = [
        {"at": "0", "message": "查看码头", "outcome": "发现湿滑脚印"},
        {"at": "1", "message": "询问船工", "outcome": "获得线索"},
    ]
    session.scene["ambient_image_state"]["last_generated_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=41)
    ).isoformat()
    by_time = ambient_image_gate(session, config)

    assert waiting["reason"] == "ambient_image_frequency_wait"
    assert waiting["min_turns"] == 30
    assert waiting["min_minutes"] == 40
    assert by_turns["ok"] is True
    assert by_time["ok"] is True


def test_prompt_template_renders_known_fields_and_keeps_unknown_placeholders():
    session = GameSession.new("group")
    session.scene["summary"] = "雨夜码头，灯塔熄灭。"
    template = "主题={story_moment}\n风格={story_style}\n未知={unknown}\nJSON={\"title\":\"x\"}"

    rendered = build_ambient_image_prompt_request(
        session,
        player_message="查看码头",
        dm_story_moment="灯塔熄灭后的码头",
        rationale="关键地点变化",
        style_seed="低饱和冷色电影感",
        prompt_template=template,
    )

    assert "主题=灯塔熄灭后的码头" in rendered
    assert "风格=低饱和冷色电影感" in rendered
    assert "未知={unknown}" in rendered
    assert 'JSON={"title":"x"}' in rendered


def test_story_style_seed_is_scoped_to_current_story_key():
    session = GameSession.new("group")
    session.scene["_game_started_at"] = "story-a"
    session.scene["ambient_image_style"] = {
        "story_key": "started:story-a",
        "description": "黑色电影，高反差雨夜",
    }

    same_story_style = _ambient_style_seed(session)
    session.scene["_game_started_at"] = "story-b"
    new_story_style = _ambient_style_seed(session)

    assert same_story_style == "黑色电影，高反差雨夜"
    assert new_story_style != "黑色电影，高反差雨夜"


def test_render_prompt_template_does_not_treat_json_braces_as_placeholders():
    rendered = render_ambient_image_prompt_template(
        '{"title":"{story_moment}","prompt":"keep {missing}"}',
        {"story_moment": "黑塔城夜雾"},
    )

    assert rendered == '{"title":"黑塔城夜雾","prompt":"keep {missing}"}'


def test_final_image_prompt_combines_title_prompt_and_style():
    prompt = compose_ambient_image_prompt(
        title="黑塔城夜雾",
        prompt="cinematic 1.5k dark tower city fog",
        style="low-key cinematic fog, restrained fantasy realism",
    )

    assert "Image title: 黑塔城夜雾" in prompt
    assert "Scene prompt:\ncinematic 1.5k dark tower city fog" in prompt
    assert "Story visual style:\nlow-key cinematic fog, restrained fantasy realism" in prompt


def test_ambient_image_tool_missing_key_skips_prompt_model():
    runtime_dir = _test_runtime_dir("missing_key")
    repo = JsonGameRepository(runtime_dir)
    session = GameSession.new("group")
    session.scene["summary"] = "雾气笼罩黑塔城。"
    session.scene["ambient_image_state"] = {
        "warmup_started_at": (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
        "interaction_count": 12,
    }
    repo.save_session(session)

    class FakeLlm:
        async def __call__(self, **kwargs):
            raise AssertionError("missing API key should skip prompt model call")

    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, api_key_env="PACKY_KEY"),
        environ={},
    )
    tools = AmbientImageTools(
        repo,
        "group",
        AmbientImageConfig(enabled=True, api_key_env="PACKY_KEY"),
        provider,
        llm_generate=FakeLlm(),
    )

    result = asyncio.run(tools.generate_ambient_image(story_moment="雾夜街道"))

    assert result["ok"] is False
    assert result["error"] == "ambient_image_api_key_missing"
    assert "last_ambient_image" not in repo.load_session("group").scene


def test_ambient_image_tool_saves_prompt_metadata_without_pending_output():
    runtime_dir = _test_runtime_dir("success")
    repo = JsonGameRepository(runtime_dir)
    session = GameSession.new("group")
    session.scene["summary"] = "黑塔城的第一夜，雾气里传来钟声。"
    session.scene["_recent_narrative_events"] = [{"at": "now", "message": "开场", "outcome": "抵达黑塔城"}]
    repo.save_session(session)

    class FakeLlm:
        async def __call__(self, **kwargs):
            return type(
                "Response",
                (),
                {
                    "completion_text": json.dumps(
                        {
                            "title": "黑塔城夜雾",
                            "prompt": "cinematic 1.5k dark tower city fog",
                            "style": "low-key cinematic fog, restrained fantasy realism",
                        }
                    )
                },
            )()

    class FakeProvider:
        async def generate(self, prompt):
            assert "Image title: 黑塔城夜雾" in prompt
            assert "Scene prompt:\ncinematic 1.5k dark tower city fog" in prompt
            assert "Story visual style:\nlow-key cinematic fog, restrained fantasy realism" in prompt
            return {
                "ok": True,
                "available": True,
                "image_bytes": b"png-bytes",
                "extension": "png",
                "api_mode": "images",
                "size": "1536x1024",
                "quality": "medium",
                "source": "b64_json",
            }

    tools = AmbientImageTools(
        repo,
        "group",
        AmbientImageConfig(enabled=True),
        FakeProvider(),
        llm_generate=FakeLlm(),
        chat_provider_id="fake-provider",
    )

    result = asyncio.run(
        tools.generate_ambient_image(
            story_moment="开场",
            rationale="新故事开场",
            trigger_override="opening",
        )
    )
    saved = repo.load_session("group")
    metadata = json.loads(Path(saved.scene["last_ambient_image"]["metadata_path"]).read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert saved.scene["last_ambient_image"]["prompt"].startswith("Image title: 黑塔城夜雾")
    assert metadata["prompt_model_prompt"] == "cinematic 1.5k dark tower city fog"
    assert saved.scene["ambient_image_style"]["description"] == "low-key cinematic fog, restrained fantasy realism"
    assert result["send_to_chat"] is True
    assert saved.scene.get("_pending_outputs", []) == []
    assert (runtime_dir / "ambient_images").exists()


def test_prompt_similarity_retry_chooses_alternate_prompt():
    runtime_dir = _test_runtime_dir("similarity_retry")
    repo = JsonGameRepository(runtime_dir)
    session = GameSession.new("group")
    session.scene["summary"] = "黑塔城的雾夜调查仍在继续。"
    repeated_prompt = compose_ambient_image_prompt(
        title="黑塔城夜雾",
        prompt="dark tower city fog bell street lantern",
        style="low key noir fantasy",
    )
    session.scene["ambient_image_prompts"] = [
        {"title": "黑塔城夜雾", "prompt": repeated_prompt},
        {"title": "黑塔城街灯", "prompt": repeated_prompt},
    ]
    session.scene["_recent_narrative_events"] = [{"at": "now", "message": "继续调查", "outcome": "抵达码头"}]
    now = datetime.now(timezone.utc).isoformat()
    session.scene["ambient_image_recent_player_messages"] = [
        {"created_at": now, "player_id": "player-a", "message": "我沿着黑塔城街灯继续搜查雾气里的钟声。"},
        {"created_at": now, "player_id": "player-b", "message": "我询问码头船工，并观察他身后潮湿木板上的蓝色灯影。"},
        {"created_at": now, "player_id": "player-c", "message": "继续"},
    ]
    repo.save_session(session)

    class FakeLlm:
        def __init__(self):
            self.prompt_calls = 0
            self.judge_calls = 0

        async def __call__(self, **kwargs):
            if "去重判定器" in str(kwargs.get("system_prompt", "")):
                self.judge_calls += 1
                score = 0.91 if self.judge_calls == 1 else 0.18
                return type(
                    "Response",
                    (),
                    {
                        "completion_text": json.dumps(
                            {
                                "score": score,
                                "too_similar": score >= 0.82,
                                "matched_recent_index": 1 if score >= 0.82 else -1,
                                "reason": "重复" if score >= 0.82 else "已换到码头船工画面",
                            }
                        )
                    },
                )()
            self.prompt_calls += 1
            if self.prompt_calls == 1:
                prompt = "dark tower city fog bell street lantern"
                title = "黑塔城夜雾"
            else:
                prompt = "rain soaked pier blue lamp distant boat silhouette wet planks"
                title = "雨夜码头蓝灯"
            return type(
                "Response",
                (),
                {"completion_text": json.dumps({"title": title, "prompt": prompt, "style": "low key noir fantasy"})},
            )()

    class FakeProvider:
        def __init__(self):
            self.prompts = []

        async def generate(self, prompt):
            self.prompts.append(prompt)
            return {
                "ok": True,
                "available": True,
                "image_bytes": b"png-bytes",
                "extension": "png",
                "api_mode": "images",
                "source": "b64_json",
            }

    fake_llm = FakeLlm()
    fake_provider = FakeProvider()
    tools = AmbientImageTools(
        repo,
        "group",
        AmbientImageConfig(enabled=True),
        fake_provider,
        actor={"player_id": "player-a"},
        message="我沿着黑塔城街灯继续搜查雾气里的钟声。",
        llm_generate=fake_llm,
    )

    result = asyncio.run(
        tools.generate_ambient_image(
            story_moment="码头调查",
            rationale="同场景需要换素材来源",
            trigger_override="opening",
        )
    )

    saved = repo.load_session("group")
    assert result["ok"] is True
    assert fake_llm.prompt_calls == 2
    assert fake_llm.judge_calls == 2
    assert len(fake_provider.prompts) == 1
    assert "rain soaked pier blue lamp" in fake_provider.prompts[0]
    assert saved.scene["last_ambient_image"]["prompt_retry_reason"] == "ambient_image_prompt_similar_recent"
    assert saved.scene["last_ambient_image"]["retry_source_player_id"] == "player-b"


def test_prompt_similarity_skips_when_retry_still_too_similar():
    runtime_dir = _test_runtime_dir("similarity_skip")
    repo = JsonGameRepository(runtime_dir)
    session = GameSession.new("group")
    session.scene["summary"] = "黑塔城的雾夜调查仍在继续。"
    repeated_prompt = compose_ambient_image_prompt(
        title="黑塔城夜雾",
        prompt="dark tower city fog bell street lantern",
        style="low key noir fantasy",
    )
    session.scene["ambient_image_prompts"] = [{"title": "黑塔城夜雾", "prompt": repeated_prompt}]
    session.scene["_recent_narrative_events"] = [{"at": "now", "message": "继续调查", "outcome": "仍在街道"}]
    now = datetime.now(timezone.utc).isoformat()
    session.scene["ambient_image_recent_player_messages"] = [
        {"created_at": now, "player_id": "player-a", "message": "我继续追查黑塔城街灯下的雾中钟声。"},
        {"created_at": now, "player_id": "player-b", "message": "我观察同一条街上潮湿石板和昏黄灯笼。"},
    ]
    repo.save_session(session)

    class FakeLlm:
        def __init__(self):
            self.prompt_calls = 0
            self.judge_calls = 0

        async def __call__(self, **kwargs):
            if "去重判定器" in str(kwargs.get("system_prompt", "")):
                self.judge_calls += 1
                return type(
                    "Response",
                    (),
                    {
                        "completion_text": json.dumps(
                            {
                                "score": 0.93,
                                "too_similar": True,
                                "matched_recent_index": 0,
                                "reason": "仍是同一条黑塔城雾街",
                            }
                        )
                    },
                )()
            self.prompt_calls += 1
            return type(
                "Response",
                (),
                {
                    "completion_text": json.dumps(
                        {
                            "title": "黑塔城夜雾",
                            "prompt": "dark tower city fog bell street lantern",
                            "style": "low key noir fantasy",
                        }
                    )
                },
            )()

    class FakeProvider:
        async def generate(self, prompt):
            raise AssertionError("provider should not be called when retry remains too similar")

    fake_llm = FakeLlm()
    tools = AmbientImageTools(
        repo,
        "group",
        AmbientImageConfig(enabled=True),
        FakeProvider(),
        actor={"player_id": "player-a"},
        message="我继续追查黑塔城街灯下的雾中钟声。",
        llm_generate=fake_llm,
    )

    result = asyncio.run(
        tools.generate_ambient_image(
            story_moment="街道调查",
            rationale="测试相似度跳过",
            trigger_override="opening",
        )
    )

    assert result["ok"] is False
    assert result["error"] == "ambient_image_prompt_too_similar"
    assert result["retry_attempted"] is True
    assert fake_llm.prompt_calls == 2
    assert fake_llm.judge_calls == 2


def test_prompt_similarity_default_compares_recent_three_prompts():
    runtime_dir = _test_runtime_dir("similarity_recent_three")
    repo = JsonGameRepository(runtime_dir)
    session = GameSession.new("group")
    session.scene["summary"] = "黑塔城的雾夜调查仍在继续。"
    old_prompt = compose_ambient_image_prompt(
        title="黑塔城夜雾",
        prompt="dark tower city fog bell street lantern",
        style="low key noir fantasy",
    )
    session.scene["ambient_image_prompts"] = [
        {"title": "更早的黑塔城夜雾", "prompt": old_prompt},
        {"title": "码头船影", "prompt": "rain pier boat blue lamp"},
        {"title": "钟楼内厅", "prompt": "clocktower interior brass stairs"},
    ]
    session.scene["_recent_narrative_events"] = [{"at": "now", "message": "回到街道", "outcome": "雾气变浓"}]
    repo.save_session(session)

    class FakeLlm:
        def __init__(self):
            self.judge_prompt = ""

        async def __call__(self, **kwargs):
            if "去重判定器" in str(kwargs.get("system_prompt", "")):
                self.judge_prompt = str(kwargs.get("prompt", ""))
                return type(
                    "Response",
                    (),
                    {"completion_text": json.dumps({"score": 0.2, "too_similar": False, "matched_recent_index": -1})},
                )()
            return type(
                "Response",
                (),
                {
                    "completion_text": json.dumps(
                        {
                            "title": "黑塔城夜雾",
                            "prompt": "dark tower city fog bell street lantern",
                            "style": "low key noir fantasy",
                        }
                    )
                },
            )()

    class FakeProvider:
        def __init__(self):
            self.called = False

        async def generate(self, prompt):
            self.called = True
            return {
                "ok": True,
                "available": True,
                "image_bytes": b"png-bytes",
                "extension": "png",
                "api_mode": "images",
                "source": "b64_json",
            }

    fake_provider = FakeProvider()
    fake_llm = FakeLlm()
    tools = AmbientImageTools(
        repo,
        "group",
        AmbientImageConfig(enabled=True),
        fake_provider,
        llm_generate=fake_llm,
    )

    result = asyncio.run(
        tools.generate_ambient_image(
            story_moment="回到街道",
            rationale="更早 prompt 不应阻止当前生成",
            trigger_override="opening",
        )
    )

    assert result["ok"] is True
    assert fake_provider.called is True
    assert "更早的黑塔城夜雾" in fake_llm.judge_prompt
    assert "码头船影" in fake_llm.judge_prompt
    assert "钟楼内厅" in fake_llm.judge_prompt


def test_prompt_similarity_recent_count_config_limits_judge_context():
    runtime_dir = _test_runtime_dir("similarity_recent_count_config")
    repo = JsonGameRepository(runtime_dir)
    session = GameSession.new("group")
    session.scene["summary"] = "黑塔城的雾夜调查仍在继续。"
    session.scene["ambient_image_prompts"] = [
        {"title": "更早的黑塔城夜雾", "prompt": "dark tower city fog bell street lantern"},
        {"title": "码头船影", "prompt": "rain pier boat blue lamp"},
        {"title": "钟楼内厅", "prompt": "clocktower interior brass stairs"},
    ]
    repo.save_session(session)

    class FakeLlm:
        def __init__(self):
            self.judge_prompt = ""

        async def __call__(self, **kwargs):
            if "去重判定器" in str(kwargs.get("system_prompt", "")):
                self.judge_prompt = str(kwargs.get("prompt", ""))
                return type(
                    "Response",
                    (),
                    {"completion_text": json.dumps({"score": 0.1, "too_similar": False})},
                )()
            return type(
                "Response",
                (),
                {
                    "completion_text": json.dumps(
                        {
                            "title": "码头蓝灯",
                            "prompt": "rain soaked pier blue lamp distant boat silhouette",
                            "style": "low key noir fantasy",
                        }
                    )
                },
            )()

    class FakeProvider:
        async def generate(self, prompt):
            return {
                "ok": True,
                "available": True,
                "image_bytes": b"png-bytes",
                "extension": "png",
                "api_mode": "images",
                "source": "b64_json",
            }

    fake_llm = FakeLlm()
    tools = AmbientImageTools(
        repo,
        "group",
        AmbientImageConfig(enabled=True, similarity_recent_count=2),
        FakeProvider(),
        llm_generate=fake_llm,
    )

    result = asyncio.run(
        tools.generate_ambient_image(
            story_moment="码头调查",
            rationale="测试相似度比较数量配置",
            trigger_override="opening",
        )
    )

    assert result["ok"] is True
    assert "更早的黑塔城夜雾" not in fake_llm.judge_prompt
    assert "码头船影" in fake_llm.judge_prompt
    assert "钟楼内厅" in fake_llm.judge_prompt

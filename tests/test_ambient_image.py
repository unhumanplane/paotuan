import asyncio
import base64
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def test_images_api_payload_defaults_to_single_high_quality_4k_and_parses_url():
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured.update({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        return 200, {"content-type": "application/json"}, json.dumps({"data": [{"url": "https://cdn.example/img.png"}]}).encode()

    def fake_get(url, headers, timeout):
        assert url == "https://cdn.example/img.png"
        return 200, {"content-type": "image/png"}, b"png-bytes"

    provider = AmbientImageProvider(
        AmbientImageConfig(enabled=True, api_key_env="PACKY_KEY"),
        environ={"PACKY_KEY": "secret"},
        http_post=fake_post,
        http_get=fake_get,
    )

    result = asyncio.run(provider.generate("古堡门廊"))

    assert result["ok"] is True
    assert captured["url"] == "https://www.packyapi.com/v1/images/generations"
    assert captured["payload"]["model"] == "gpt-image-2"
    assert captured["payload"]["n"] == 1
    assert captured["payload"]["size"] == "3840x2160"
    assert captured["payload"]["quality"] == "high"
    assert captured["timeout"] == 120
    assert result["image_bytes"] == b"png-bytes"
    assert result["extension"] == "png"


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


def test_direct_user_image_request_does_not_trigger_ambient_image():
    session = GameSession.new("group")
    session.scene["summary"] = "雾气笼罩黑塔城，城门前响起钟声。"
    session.scene["ambient_image_state"] = {
        "warmup_started_at": (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
        "interaction_count": 12,
    }
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


def test_ambient_image_tool_saves_prompt_metadata_and_pending_output():
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
                            "prompt": "cinematic 4k dark tower city fog",
                            "style": "low-key cinematic fog, restrained fantasy realism",
                        }
                    )
                },
            )()

    class FakeProvider:
        async def generate(self, prompt):
            assert prompt == "cinematic 4k dark tower city fog"
            return {
                "ok": True,
                "available": True,
                "image_bytes": b"png-bytes",
                "extension": "png",
                "api_mode": "images",
                "size": "3840x2160",
                "quality": "high",
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

    assert result["ok"] is True
    assert saved.scene["last_ambient_image"]["prompt"] == "cinematic 4k dark tower city fog"
    assert saved.scene["ambient_image_style"]["description"] == "low-key cinematic fog, restrained fantasy realism"
    assert saved.scene["_pending_outputs"][0]["type"] == "ambient_image"
    assert (runtime_dir / "ambient_images").exists()

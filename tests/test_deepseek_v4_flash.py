from __future__ import annotations

import asyncio
import copy
import json
import os

import pytest

from astrbot_plugin_auto_trpg_dm.core.deepseek_v4_flash import (
    DEFAULT_DEEPSEEK_MODEL,
    DeepSeekV4FlashClient,
    DeepSeekV4FlashConfig,
    build_messages,
    complete,
    complete_or_raise,
    finish_reason,
    reasoning_content,
    response_text,
)


def test_missing_api_key_degrades_without_call():
    calls: list[tuple[str, dict[str, str], dict[str, object], int]] = []
    client = DeepSeekV4FlashClient(
        DeepSeekV4FlashConfig(enabled=True, api_key_env="DEEPSEEK_API_KEY"),
        environ={},
        http_post=lambda *args: calls.append(args),
    )

    result = client.call_chat_completion([{"role": "user", "content": "hello"}])

    assert result["ok"] is False
    assert result["error"] == "deepseek_api_key_missing"
    assert calls == []


def test_direct_key_uses_expected_headers_and_payload():
    captured: dict[str, object] = {}

    def fake_post(url, headers, payload, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = copy.deepcopy(payload)
        captured["timeout"] = timeout
        body = {
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "message": {
                        "content": "OK",
                        "reasoning_content": "think",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        }
        return 200, {"content-type": "application/json"}, json.dumps(body).encode("utf-8")

    client = DeepSeekV4FlashClient(
        DeepSeekV4FlashConfig(enabled=True, api_key="direct-secret"),
        environ={},
        http_post=fake_post,
    )

    result = client.call_chat_completion(
        [{"role": "user", "content": "hello"}],
        max_tokens=64,
        temperature=0.1,
        json_mode=True,
        thinking="disabled",
    )

    assert result["ok"] is True
    assert result["text"] == "OK"
    assert result["reasoning_content"] == "think"
    assert result["finish_reason"] == "stop"
    assert result["usage"]["prompt_tokens"] == 12
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer direct-secret"
    assert captured["payload"]["model"] == DEFAULT_DEEPSEEK_MODEL
    assert captured["payload"]["messages"][0]["content"] == "hello"
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["timeout"] == 120


def test_chat_completion_uses_fallbacks_until_request_succeeds():
    attempts: list[dict[str, object]] = []

    def fake_post(url, headers, payload, timeout):
        attempts.append(copy.deepcopy(payload))
        if "thinking" in payload and "response_format" in payload:
            return 400, {}, b'{"error":"thinking and json mode failed"}'
        if "thinking" in payload:
            return 422, {}, b'{"error":"thinking failed"}'
        if "response_format" in payload:
            return 422, {}, b'{"error":"json mode failed"}'
        body = {
            "choices": [
                {
                    "message": {
                        "content": "done",
                        "reasoning_content": "final reasoning",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10},
        }
        return 200, {"content-type": "application/json"}, json.dumps(body).encode("utf-8")

    client = DeepSeekV4FlashClient(
        DeepSeekV4FlashConfig(enabled=True, api_key="direct-secret"),
        environ={},
        http_post=fake_post,
    )

    result = client.call_chat_completion(
        [{"role": "user", "content": "hello"}],
        json_mode=True,
        thinking="enabled",
    )

    assert result["ok"] is True
    assert result["request_fallbacks"] == ["without_thinking_or_json_mode"]
    assert len(attempts) == 4
    assert "thinking" in attempts[0]
    assert "response_format" in attempts[0]
    assert "thinking" not in attempts[-1]
    assert "response_format" not in attempts[-1]
    assert result["text"] == "done"
    assert result["reasoning_content"] == "final reasoning"


def test_message_helpers_and_complete_helper_preserve_prompt_shape():
    captured: dict[str, object] = {}

    def fake_post(url, headers, payload, timeout):
        captured["payload"] = copy.deepcopy(payload)
        body = {
            "choices": [{"message": {"content": "hello there"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1},
        }
        return 200, {"content-type": "application/json"}, json.dumps(body).encode("utf-8")

    messages = build_messages(
        "final question",
        system_prompt="you are concise",
        contexts=[{"role": "assistant", "content": "context"}, "legacy context"],
    )

    assert messages[0] == {"role": "system", "content": "you are concise"}
    assert messages[-1] == {"role": "user", "content": "final question"}
    assert messages[1] == {"role": "assistant", "content": "context"}
    assert messages[2] == {"role": "system", "content": "legacy context"}

    result = complete(
        "final question",
        system_prompt="you are concise",
        contexts=[{"role": "assistant", "content": "context"}],
        api_key="direct-secret",
        http_post=fake_post,
    )

    assert result["ok"] is True
    assert result["text"] == "hello there"
    assert captured["payload"]["messages"][0]["role"] == "system"
    assert captured["payload"]["messages"][-1]["content"] == "final question"


def test_response_helpers_extract_reasoning_and_finish_reason():
    response = {
        "choices": [
            {
                "message": {
                    "content": "hello",
                    "reasoning_content": "thinking",
                },
                "finish_reason": "stop",
            }
        ]
    }

    assert response_text(response) == "hello"
    assert reasoning_content(response) == "thinking"
    assert finish_reason(response) == "stop"


@pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY is not set")
def test_deepseek_live_smoke():
    result = complete_or_raise(
        "Reply with exactly the single word OK.",
        system_prompt="You are a concise assistant.",
        api_key_env="DEEPSEEK_API_KEY",
        json_mode=False,
        thinking="disabled",
        temperature=0,
        max_tokens=8,
        timeout=30,
    )

    assert result["ok"] is True
    assert result["available"] is True
    assert result["text"].strip()

import hashlib
import hmac
import json
import sys
import types


def _install_fake_astrbot_modules():
    if "astrbot.api" in sys.modules:
        return
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    star = types.ModuleType("astrbot.api.star")
    core = types.ModuleType("astrbot.core")
    core_star = types.ModuleType("astrbot.core.star")
    filter_pkg = types.ModuleType("astrbot.core.star.filter")
    command = types.ModuleType("astrbot.core.star.filter.command")

    class FakeLogger:
        def info(self, *args, **kwargs):
            pass
        def warning(self, *args, **kwargs):
            pass

    class FakeFilter:
        @staticmethod
        def command(*args, **kwargs):
            return lambda fn: fn

    class FakeStar:
        def __init__(self, context=None):
            self.context = context

    def register(*args, **kwargs):
        return lambda cls: cls

    class GreedyStr(str):
        pass

    api.logger = FakeLogger()
    event.AstrMessageEvent = object
    event.filter = FakeFilter
    star.Context = object
    star.Star = FakeStar
    star.register = register
    command.GreedyStr = GreedyStr

    for name, module in {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.star": star,
        "astrbot.core": core,
        "astrbot.core.star": core_star,
        "astrbot.core.star.filter": filter_pkg,
        "astrbot.core.star.filter.command": command,
    }.items():
        sys.modules[name] = module


_install_fake_astrbot_modules()

from astrbot_plugin_hermes_coder.main import HermesCoderPlugin


class FakeEvent:
    unified_msg_origin = "aiocqhttp:GroupMessage:676453921"

    def __init__(self, group_id="676453921", sender_id="1903948152"):
        self._group_id = group_id
        self._sender_id = sender_id
        self.message_obj = type("MessageObj", (), {"message_id": "msg1", "group_id": group_id})()

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return self._sender_id


def test_group_whitelist_normalizes_values():
    plugin = HermesCoderPlugin.__new__(HermesCoderPlugin)
    plugin.config = {"group_whitelist": [676453921, " 1027178094 "], "bridge_secret": "secret"}
    plugin.group_whitelist = plugin._config_str_set("group_whitelist")

    assert plugin._event_group_id(FakeEvent()) == "676453921"
    assert "676453921" in plugin.group_whitelist
    assert "1027178094" in plugin.group_whitelist


def test_empty_greedystr_sentinel_is_usage_prompt():
    assert HermesCoderPlugin._prompt_from_command_content(None) == ""
    assert HermesCoderPlugin._prompt_from_command_content("  hello  ") == "hello"

    class GreedyStrSentinel:
        def __str__(self):
            return "GreedyStr"

    assert HermesCoderPlugin._prompt_from_command_content(GreedyStrSentinel()) == ""
    assert HermesCoderPlugin._prompt_from_command_content("GreedyStr") == "GreedyStr"


def test_bridge_signature_uses_compact_json(monkeypatch):
    plugin = HermesCoderPlugin.__new__(HermesCoderPlugin)
    plugin.bridge_secret = "secret"
    plugin.bridge_url = "http://bridge/coder"
    plugin.timeout_seconds = 240
    captured = {}

    class FakeResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self):
            return b'{"reply":"ok"}'

    def fake_urlopen(req, timeout):
        captured["body"] = req.data
        captured["signature"] = req.headers["X-hermes-coder-signature"]
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    payload = {"prompt": "hi", "group_id": "676453921"}
    assert plugin._post_bridge(payload)["reply"] == "ok"
    expected = hmac.new(
        b"secret",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert captured["body"] == b'{"prompt":"hi","group_id":"676453921"}'
    assert captured["signature"] == expected

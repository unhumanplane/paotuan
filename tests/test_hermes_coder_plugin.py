import asyncio
import hashlib
import hmac
import json
import logging
import sys
import types


def _install_fake_astrbot_modules():
    if "astrbot.api" in sys.modules:
        return
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    event_filter = types.ModuleType("astrbot.api.event.filter")
    star = types.ModuleType("astrbot.api.star")
    core = types.ModuleType("astrbot.core")
    core_utils = types.ModuleType("astrbot.core.utils")
    message = types.ModuleType("astrbot.core.message")
    components = types.ModuleType("astrbot.core.message.components")
    message_event_result = types.ModuleType("astrbot.core.message.message_event_result")
    astrbot_path = types.ModuleType("astrbot.core.utils.astrbot_path")
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

    class Plain:
        def __init__(self, text=""):
            self.text = text

    class File:
        def __init__(self, name="", file=""):
            self.name = name
            self.file = file

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = chain or []

    def get_astrbot_data_path():
        return "."

    api.logger = FakeLogger()
    event.AstrMessageEvent = object
    event.filter = FakeFilter
    event_filter.regex = lambda *args, **kwargs: (lambda fn: fn)
    star.Context = object
    star.Star = FakeStar
    star.register = register
    astrbot_path.get_astrbot_data_path = get_astrbot_data_path
    command.GreedyStr = GreedyStr
    components.File = File
    components.Plain = Plain
    message_event_result.MessageChain = MessageChain

    for name, module in {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.event.filter": event_filter,
        "astrbot.api.star": star,
        "astrbot.core": core,
        "astrbot.core.utils": core_utils,
        "astrbot.core.message": message,
        "astrbot.core.message.components": components,
        "astrbot.core.message.message_event_result": message_event_result,
        "astrbot.core.utils.astrbot_path": astrbot_path,
        "astrbot.core.star": core_star,
        "astrbot.core.star.filter": filter_pkg,
        "astrbot.core.star.filter.command": command,
    }.items():
        sys.modules[name] = module


_install_fake_astrbot_modules()

from astrbot_plugin_hermes_coder.main import HermesCoderPlugin
from astrbot_plugin_hermes_coder.main import configure_coder_logging


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, session_id, chain):
        self.sent.append((session_id, chain))
        return True

class FakeBot:
    def __init__(self):
        self.actions = []

    async def call_action(self, action, **kwargs):
        self.actions.append((action, kwargs))
        return {"status": "ok"}

class FakeEvent:
    unified_msg_origin = "aiocqhttp:GroupMessage:676453921"

    def __init__(self, group_id="676453921", sender_id="1903948152", bot=None):
        self._group_id = group_id
        self._sender_id = sender_id
        self.bot = bot
        self.message_obj = type("MessageObj", (), {"message_id": "msg1", "group_id": group_id})()

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return self._sender_id

    def plain_result(self, text):
        return {"kind": "plain", "text": text}

    def chain_result(self, components):
        return {"kind": "chain", "components": components}

    def stop_event(self):
        self.stopped = True


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


def test_raw_coder_prefix_extracts_prompt_without_space():
    assert HermesCoderPlugin._prompt_from_raw_message("/coder 审查 PR") == "审查 PR"
    assert HermesCoderPlugin._prompt_from_raw_message("/coder审查 PR") == "审查 PR"
    assert HermesCoderPlugin._prompt_from_raw_message("／coder 审查 PR") == "审查 PR"


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


def test_configure_coder_logging_writes_independent_file(tmp_path):
    log_path = tmp_path / "logs" / "hermes_coder.log"
    plugin_logger = configure_coder_logging(log_path, max_bytes=20_000, backup_count=1)

    plugin_logger.info("request_started group=%s prompt_chars=%s", "1101538762", 2)
    for handler in plugin_logger.handlers:
        handler.flush()

    text = log_path.read_text(encoding="utf-8")
    assert "request_started group=1101538762 prompt_chars=2" in text
    assert plugin_logger.propagate is False
    assert any(isinstance(handler, logging.Handler) for handler in plugin_logger.handlers)


def test_immediate_ack_uses_context_send_message():
    async def run_case():
        context = FakeContext()
        plugin = HermesCoderPlugin.__new__(HermesCoderPlugin)
        plugin.context = context
        plugin.ack_enabled = True
        plugin.ack_text = "处理中"
        plugin.coder_logger = logging.getLogger("test-hermes-coder-ack")

        payload = {
            "group_id": "676453921",
            "sender_id": "1903948152",
            "session_id": "aiocqhttp:GroupMessage:676453921",
            "message_id": "msg1",
        }

        await plugin._send_immediate_ack(FakeEvent(), payload)

        assert len(context.sent) == 1
        session_id, chain = context.sent[0]
        assert session_id == "aiocqhttp:GroupMessage:676453921"
        assert "".join(getattr(item, "text", "") for item in chain.chain) == "处理中"

    asyncio.run(run_case())


def test_response_file_components_only_allow_configured_export_prefixes():
    plugin = HermesCoderPlugin.__new__(HermesCoderPlugin)
    plugin.file_send_enabled = True
    plugin.file_send_path_prefixes = {"/AstrBot/data/plugin_data/astrbot_plugin_hermes_coder/exports/"}
    plugin.coder_logger = logging.getLogger("test-hermes-coder-files")

    components = plugin._response_file_components(
        {
            "files": [
                {
                    "path": "/AstrBot/data/plugin_data/astrbot_plugin_hermes_coder/exports/game_logs/latest.txt",
                    "name": "../latest.txt",
                },
                {
                    "path": "/AstrBot/data/private/secret.txt",
                    "name": "secret.txt",
                },
            ]
        }
    )

    assert len(components) == 1
    assert getattr(components[0], "name") == "latest.txt"
    assert getattr(components[0], "file") == "/AstrBot/data/plugin_data/astrbot_plugin_hermes_coder/exports/game_logs/latest.txt"


def test_handle_coder_uploads_export_file_directly_for_aiocqhttp(monkeypatch):
    async def run_case():
        plugin = HermesCoderPlugin.__new__(HermesCoderPlugin)
        plugin.enabled = True
        plugin.group_whitelist = {"676453921"}
        plugin.allow_private_chat = False
        plugin.bridge_secret = "secret"
        plugin.timeout_seconds = 5
        plugin.max_prompt_chars = 4000
        plugin.max_reply_chars = 3500
        plugin.ack_enabled = False
        plugin.file_send_enabled = True
        plugin.file_send_path_prefixes = {"/AstrBot/data/plugin_data/astrbot_plugin_hermes_coder/exports/"}
        plugin.coder_logger = logging.getLogger("test-hermes-coder-file-result")

        def fake_post_bridge(payload):
            assert payload["prompt"] == "获取最新游戏日志"
            return {
                "ok": True,
                "reply": "日志导出好了",
                "files": [
                    {
                        "path": "/AstrBot/data/plugin_data/astrbot_plugin_hermes_coder/exports/game_logs/latest.txt",
                        "name": "latest.txt",
                    }
                ],
            }

        monkeypatch.setattr(plugin, "_post_bridge", fake_post_bridge)

        bot = FakeBot()
        results = []
        async for result in plugin._handle_coder(FakeEvent(bot=bot), "获取最新游戏日志"):
            results.append(result)

        assert len(results) == 1
        assert results[0] == {"kind": "plain", "text": "日志导出好了"}
        assert bot.actions == [
            (
                "upload_group_file",
                {
                    "group_id": 676453921,
                    "file": "/AstrBot/data/plugin_data/astrbot_plugin_hermes_coder/exports/game_logs/latest.txt",
                    "name": "latest.txt",
                },
            )
        ]

    asyncio.run(run_case())

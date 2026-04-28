from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from time import monotonic
from typing import List

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.message.components import Image as ImageComponent, Plain, Reply
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core.plugin_log import configure_plugin_logging
from .core.router import IntentRouter
from .core.security import security_precheck
from .rules.python_runtime import PythonRuleRuntime
from .storage.json_repository import JsonGameRepository
from .tools.diagnostic_tools import DiagnosticTools
from .tools.registry import ToolRegistry


@register(
    "auto_trpg_dm",
    "codex",
    "全自然语言 TRPG DM：动态规则、战棋物理验证、Tag 角色卡与自动剧本。",
    "0.1.21",
)
class AutoTrpgDmPlugin(Star):
    DEDUP_WINDOW_SECONDS = 18.0

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self.trigger_prefixes = ["/dm"]
        self._recent_dm_messages: dict[tuple[str, str, str], float] = {}
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_auto_trpg_dm"
        self.repository = JsonGameRepository(data_dir)
        self.plugin_logger = configure_plugin_logging(self.repository.plugin_log_path())
        rule_runtime = PythonRuleRuntime(data_dir / "rules")
        tool_registry = ToolRegistry(repository=self.repository, rule_runtime=rule_runtime, astr_context=context)
        self.router = IntentRouter(
            astr_context=context,
            repository=self.repository,
            tool_registry=tool_registry,
        )
        migrated = self._migrate_legacy_turn_fields()
        if migrated:
            self.plugin_logger.info("legacy_turn_fields_migrated saves=%s", migrated)
        self.plugin_logger.info("plugin_initialized version=0.1.21 data_dir=%s", data_dir)
        logger.info("Auto TRPG DM plugin initialized.")

    @filter.command("dm")
    async def on_dm_command(self, event: AstrMessageEvent, content: GreedyStr):
        """唯一显式入口：/dm 后面全部交给自然语言路由。"""
        async for result in self._handle_dm_command_content(event, content):
            yield result

    @filter.command("DM")
    async def on_dm_command_upper(self, event: AstrMessageEvent, content: GreedyStr):
        """兼容玩家误用 /DM，避免请求落到 AstrBot 默认聊天。"""
        async for result in self._handle_dm_command_content(event, content):
            yield result

    @filter.command("Dm")
    async def on_dm_command_title(self, event: AstrMessageEvent, content: GreedyStr):
        """兼容玩家误用 /Dm。"""
        async for result in self._handle_dm_command_content(event, content):
            yield result

    @filter.command("dM")
    async def on_dm_command_mixed(self, event: AstrMessageEvent, content: GreedyStr):
        """兼容玩家误用 /dM。"""
        async for result in self._handle_dm_command_content(event, content):
            yield result

    async def _handle_dm_command_content(self, event: AstrMessageEvent, content: GreedyStr):
        routed_message = str(content or "").strip()
        if not routed_message:
            routed_message = "查看当前跑团状态；如果还没有开局，请询问玩家想跑什么类型的团。"
        async for result in self._handle_dm_event(event, routed_message):
            yield result

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_any_message(self, event: AstrMessageEvent):
        """只接收显式 /dm 入口，避免普通群聊进入 LLM。"""
        message = (event.message_str or "").strip()
        if not message:
            return
        routed_message = self._extract_routed_message(event, message)
        if not routed_message:
            return
        async for result in self._handle_dm_event(event, routed_message):
            yield result

    async def _handle_dm_event(self, event: AstrMessageEvent, routed_message: str):
        session_id = IntentRouter.session_id_for_event(event)
        actor = self.router.actor_context_for_event(event)
        sender_id = actor.get("player_id", "")
        fast_reply = await self._local_fast_path(session_id, actor, routed_message)
        if fast_reply:
            self.plugin_logger.info(
                "dm_fast_path session=%s sender=%s text=%s",
                session_id,
                sender_id,
                self._dedupe_text(routed_message)[:160],
            )
            yield self._quoted_result(event, fast_reply)
            event.stop_event()
            return
        duplicate_reply = self._duplicate_reply(session_id, sender_id, routed_message)
        if duplicate_reply:
            self.plugin_logger.info(
                "duplicate_dm_ignored session=%s sender=%s text=%s",
                session_id,
                sender_id,
                self._dedupe_text(routed_message)[:160],
            )
            yield self._quoted_result(event, duplicate_reply)
            event.stop_event()
            return
        security = security_precheck(routed_message)
        if security.blocked:
            self.plugin_logger.info(
                "dm_received session=%s sender=%s risk=%s text=<blocked>",
                session_id,
                sender_id,
                security.risk,
            )
            logger.info(
                "Auto TRPG DM received /dm message. session=%s sender=%s text=<blocked>",
                session_id,
                sender_id,
            )
        else:
            self.plugin_logger.info(
                "dm_received session=%s sender=%s text=%s",
                session_id,
                sender_id,
                security.redacted_message[:240].replace("\n", "\\n"),
            )
            logger.info(
                "Auto TRPG DM handling /dm message. session=%s sender=%s text=%s",
                session_id,
                sender_id,
                security.redacted_message[:120],
            )
        if security.blocked:
            self.plugin_logger.warning(
                "security_block session=%s sender=%s risk=%s categories=%s",
                session_id,
                sender_id,
                security.risk,
                ",".join(security.categories),
            )
            logger.warning(
                "Auto TRPG DM blocked unsafe /dm message. session=%s sender=%s risk=%s categories=%s",
                session_id,
                sender_id,
                security.risk,
                ",".join(security.categories),
            )
            self.repository.append_audit(
                session_id,
                {
                    "type": "security_block",
                    "actor": actor,
                    **security.to_audit_record(),
                },
            )
            yield self._quoted_result(event, security.reply)
            event.stop_event()
            return
        if security.notes:
            self.plugin_logger.info(
                "security_note session=%s sender=%s categories=%s notes=%s",
                session_id,
                sender_id,
                ",".join(security.categories),
                len(security.notes),
            )
            logger.info(
                "Auto TRPG DM marked /dm message for guarded adjudication. session=%s sender=%s categories=%s",
                session_id,
                sender_id,
                ",".join(security.categories),
            )
            self.repository.append_audit(
                session_id,
                {
                    "type": "security_note",
                    "actor": actor,
                    **security.to_audit_record(),
                },
            )
        try:
            completion = await self.router.handle_message(
                event,
                message_override=routed_message,
                security_notes=security.notes,
            )
        except Exception as exc:
            self.plugin_logger.exception("dm_failed session=%s sender=%s error=%s", session_id, sender_id, exc)
            logger.exception("Auto TRPG DM failed to handle message.")
            yield self._quoted_result(event, self._friendly_error_message(exc))
            event.stop_event()
            return
        pending_outputs = self._pop_pending_outputs(session_id)
        dice_outputs = [item for item in pending_outputs if item.get("type") == "dice_check"]
        other_outputs = [item for item in pending_outputs if item.get("type") != "dice_check"]
        sent_any = False
        for item in dice_outputs[:3]:
            dice_text = self._format_dice_check(item)
            if dice_text:
                yield self._quoted_result(event, dice_text)
                sent_any = True
        if completion or other_outputs:
            if not completion and other_outputs:
                completion = "地图已生成，已附上。"
            self.plugin_logger.info(
                "dm_completed session=%s sender=%s reply_chars=%s pending_outputs=%s",
                session_id,
                sender_id,
                len(completion),
                len(pending_outputs),
            )
            yield self._quoted_result(event, completion, pending_outputs=other_outputs)
            sent_any = True
        if sent_any:
            event.stop_event()

    async def _local_fast_path(self, session_id: str, actor: dict[str, str], routed_message: str) -> str:
        text = self._dedupe_text(routed_message)
        normalized = text.lower()
        session = self.repository.load_session(session_id)
        paused = bool((session.scene or {}).get("_dm_paused", False))

        if normalized in {"pause", "暂停", "暂停流程", "暂停游戏"}:
            session.scene["_dm_paused"] = True
            session.scene["_dm_pause_reason"] = text
            session.scene["_dm_paused_by"] = actor
            session.scene["_dm_paused_at"] = _utc_now_iso()
            self.repository.save_session(session)
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "pause", "actor": actor})
            return "流程已暂停。我不会推进轮次、替人行动或调用模型；需要继续时发 `/dm resume` 或 `/dm 恢复`。"

        if normalized in {"resume", "unpause", "恢复", "继续流程", "解除暂停"} or (paused and normalized == "继续"):
            if paused:
                session.scene["_dm_paused"] = False
                session.scene["_dm_resumed_by"] = actor
                session.scene["_dm_resumed_at"] = _utc_now_iso()
                self.repository.save_session(session)
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "resume", "actor": actor})
            return "流程已恢复。下一句 `/dm` 会按当前存档继续裁定。"

        if normalized in {"status", "状态", "当前状态"}:
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "status", "actor": actor})
            return self._format_local_status(session)

        if normalized in {"token", "tokens", "token消耗", "上下文", "上下文消耗"}:
            usage = await DiagnosticTools(self.repository, session_id).estimate_token_usage("summary")
            current = usage.get("current", {})
            rough = usage.get("rough_token_estimate", {})
            compression = usage.get("compression", {})
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "token", "actor": actor})
            return (
                "Token 粗算："
                f"快照 {current.get('compact_snapshot_chars', 0)} 字，约 {rough.get('heuristic', 0)} token；"
                f"完整存档 {current.get('full_save_chars', 0)} 字。"
                f"距自动压缩约 {compression.get('snapshot_chars_remaining_before_compression', 0)} 字。"
            )

        if normalized in {"当前轮次", "当前回合", "轮次", "回合", "谁行动", "轮到谁"}:
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "turn_status", "actor": actor})
            return self._format_turn_status(session)

        if paused:
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "paused_block", "actor": actor})
            return "当前流程处于暂停状态，我不会把这句送进模型。可用 `/dm status`、`/dm token`、`/dm 当前轮次` 查看信息，或 `/dm resume` 恢复。"

        return ""

    def _format_local_status(self, session) -> str:
        battle = session.battle or {}
        turn = dict(battle.get("turn") or {})
        paused = "暂停中" if (session.scene or {}).get("_dm_paused") else "运行中"
        if turn.get("active"):
            turn_text = self._format_turn_status(session)
        else:
            turn_text = "当前没有启用轮次。"
        return (
            f"团名：{session.title}；模式：{session.mode.value}；流程：{paused}。\n"
            f"玩家 {len(session.participants)}，角色 {len(session.characters)}，规则 {len(session.rules)}。\n"
            f"{turn_text}"
        )

    def _format_turn_status(self, session) -> str:
        battle = session.battle or {}
        turn = dict(battle.get("turn") or {})
        if not turn.get("active"):
            return "当前没有启用轮次。"
        current_id = str(turn.get("current_entity_id") or battle.get("turn_entity_id") or "")
        entities = dict((battle.get("grid") or {}).get("entities") or {})
        label = _entity_label(session, current_id, entities) if current_id else "未指定"
        owner_id = _entity_owner(session, current_id, entities) if current_id else ""
        owner_name = str((session.participants.get(owner_id) or {}).get("display_name") or owner_id or "未绑定")
        deadline = _parse_datetime(turn.get("deadline_at"))
        if deadline:
            remaining = max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))
            wait_text = f"等待剩余约 {remaining} 秒。"
        else:
            wait_text = "等待计时会在下一次相关 /dm 时补齐为 120 秒。"
        return (
            f"第 {int(turn.get('round') or 0)} 轮，阶段：{turn.get('phase', 'idle')}。\n"
            f"当前行动：{label}（{current_id or '无'}），持有人：{owner_name}。\n"
            f"{wait_text}"
        )

    def _migrate_legacy_turn_fields(self) -> int:
        migrated = 0
        now = datetime.now(timezone.utc)
        for path in self.repository.saves_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.plugin_logger.warning("legacy_turn_migration_read_failed path=%s error=%s", path, exc)
                continue
            battle = data.get("battle")
            if not isinstance(battle, dict):
                continue
            turn = battle.get("turn")
            if not isinstance(turn, dict) or not turn.get("active"):
                continue
            changed = False
            if not turn.get("timeout_seconds"):
                turn["timeout_seconds"] = 120
                changed = True
            if str(turn.get("phase", "")) == "character_turn":
                current_id = str(turn.get("current_entity_id") or battle.get("turn_entity_id") or "")
                if current_id and not battle.get("turn_entity_id"):
                    battle["turn_entity_id"] = current_id
                    changed = True
                if not turn.get("waiting_since_at"):
                    turn["waiting_since_at"] = now.isoformat()
                    changed = True
                if not turn.get("deadline_at"):
                    turn["deadline_at"] = (now + timedelta(seconds=120)).isoformat()
                    changed = True
                if not turn.get("wait_reset_reason"):
                    turn["wait_reset_reason"] = "legacy_migration"
                    changed = True
            if not changed:
                continue
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            migrated += 1
            session_id = str(data.get("session_id") or path.stem)
            try:
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "legacy_turn_fields_migrated",
                        "save": path.name,
                        "timeout_seconds": turn.get("timeout_seconds"),
                        "deadline_at": turn.get("deadline_at", ""),
                    },
                )
            except Exception as exc:
                self.plugin_logger.warning("legacy_turn_migration_audit_failed path=%s error=%s", path, exc)
        return migrated

    async def terminate(self):
        self.plugin_logger.info("plugin_terminated")
        logger.info("Auto TRPG DM plugin terminated.")

    def _extract_routed_message(self, event: AstrMessageEvent, message: str) -> str:
        stripped = message.strip()
        lowered = stripped.lower()
        for prefix in self.trigger_prefixes:
            prefix_lower = prefix.lower()
            if lowered == prefix_lower:
                return ""
            if lowered.startswith(prefix_lower + " ") or lowered.startswith(prefix_lower + "\n"):
                return stripped[len(prefix) :].strip()
        return ""

    def _quoted_result(self, event: AstrMessageEvent, text: str, pending_outputs: list[dict] | None = None):
        message_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        pending_outputs = pending_outputs or []
        components = []
        if not message_id:
            if not pending_outputs:
                return event.plain_result(text)
        else:
            components.append(Reply(id=message_id))
        components.append(Plain(text=text))
        for item in pending_outputs[:2]:
            if item.get("type") != "svg_map":
                continue
            file_path = str(item.get("path", ""))
            name = str(item.get("name", "") or "trpg_map.svg")
            if file_path:
                png_path = self._ensure_png_preview(file_path, item)
                if png_path:
                    components.append(ImageComponent.fromFileSystem(png_path))
                    self.plugin_logger.info("map_preview_attached path=%s", png_path)
                else:
                    components.append(Plain(text=f"\n地图已生成：{name}\n{file_path}"))
        return event.chain_result(components)

    def _format_dice_check(self, item: dict) -> str:
        rolls = item.get("rolls") or []
        if not rolls:
            return ""
        reason = _compact_text(item.get("reason") or "本轮行动需要随机裁定", 120)
        rule_name = _compact_text(item.get("rule_name") or "unknown_rule", 80)
        version = item.get("version")
        roll_text = "；".join(_format_roll_record(record) for record in rolls[:6])
        if len(rolls) > 6:
            roll_text += f"；另有 {len(rolls) - 6} 次掷骰"
        if item.get("ok"):
            result_text = _compact_result(item.get("rule_result"))
        else:
            result_text = _compact_text(item.get("error_reason") or item.get("error") or "规则执行失败", 160)
        suffix = f" v{version}" if version else ""
        return f"骰子检定：{reason}\n规则：{rule_name}{suffix}\n掷骰：{roll_text}\n结果：{result_text}"

    def _ensure_png_preview(self, svg_path: str, item: dict) -> str:
        path = Path(svg_path)
        if not path.exists():
            self.plugin_logger.warning("map_preview_missing_source path=%s", svg_path)
            return ""
        png_path = path.with_suffix(".png")
        if png_path.exists() and png_path.stat().st_mtime >= path.stat().st_mtime:
            return str(png_path)
        try:
            self._render_svg_preview(path, png_path, int(item.get("width") or 900), int(item.get("height") or 900))
            return str(png_path)
        except Exception as exc:
            self.plugin_logger.warning("map_preview_render_failed path=%s error=%s", svg_path, exc)
            return ""

    @staticmethod
    def _render_svg_preview(svg_path: Path, png_path: Path, fallback_width: int, fallback_height: int) -> None:
        from PIL import Image, ImageColor, ImageDraw, ImageFont

        root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
        width = _svg_int(root.get("width"), fallback_width)
        height = _svg_int(root.get("height"), fallback_height)
        width = max(320, min(1600, width))
        height = max(320, min(1600, height))
        canvas = Image.new("RGB", (width, height), "#f8fafc")
        draw = ImageDraw.Draw(canvas)
        font_cache = {}

        def font_for(size: int, weight: object = ""):
            size = max(10, min(48, int(size or 16)))
            bold = str(weight or "").lower() in {"bold", "700", "800", "900"}
            key = (size, bold)
            if key in font_cache:
                return font_cache[key]
            for candidate in _font_candidates(svg_path, bold=bold):
                try:
                    font_cache[key] = ImageFont.truetype(str(candidate), size)
                    return font_cache[key]
                except Exception:
                    continue
            try:
                font_cache[key] = ImageFont.truetype("DejaVuSans.ttf", size)
            except Exception:
                font_cache[key] = ImageFont.load_default()
            return font_cache[key]

        def color(value: object, default: str = "#111827"):
            text = str(value or "").strip()
            if not text or text.lower() == "none":
                return None
            if text.startswith("url("):
                return default
            try:
                return ImageColor.getrgb(text)
            except Exception:
                return default

        def walk(element: ET.Element) -> None:
            tag = _svg_local_name(element.tag)
            fill = color(element.get("fill"), "#e5e7eb")
            stroke = color(element.get("stroke"), "#111827")
            stroke_width = max(1, _svg_int(element.get("stroke-width"), 1))
            if tag == "rect":
                x = _svg_float(element.get("x"))
                y = _svg_float(element.get("y"))
                w = _svg_float(element.get("width"))
                h = _svg_float(element.get("height"))
                draw.rectangle([x, y, x + w, y + h], fill=fill, outline=stroke, width=stroke_width)
            elif tag == "line":
                draw.line(
                    [
                        (_svg_float(element.get("x1")), _svg_float(element.get("y1"))),
                        (_svg_float(element.get("x2")), _svg_float(element.get("y2"))),
                    ],
                    fill=stroke or "#111827",
                    width=stroke_width,
                )
            elif tag in {"circle", "ellipse"}:
                cx = _svg_float(element.get("cx"))
                cy = _svg_float(element.get("cy"))
                rx = _svg_float(element.get("rx"), _svg_float(element.get("r"), 0))
                ry = _svg_float(element.get("ry"), _svg_float(element.get("r"), 0))
                draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=fill, outline=stroke, width=stroke_width)
            elif tag in {"polygon", "polyline"}:
                points = _svg_points(element.get("points"))
                if len(points) >= 2:
                    if tag == "polygon":
                        draw.polygon(points, fill=fill, outline=stroke)
                    else:
                        draw.line(points, fill=stroke or "#111827", width=stroke_width)
            elif tag == "text":
                text = "".join(element.itertext()).strip()
                if text:
                    font = font_for(
                        _svg_int(element.get("font-size"), 18),
                        element.get("font-weight"),
                    )
                    x = _svg_float(element.get("x"))
                    y = _svg_float(element.get("y"))
                    try:
                        bbox = draw.textbbox((0, 0), text[:40], font=font)
                        text_width = bbox[2] - bbox[0]
                        text_height = bbox[3] - bbox[1]
                    except Exception:
                        text_width = 0
                        text_height = 0
                    anchor = str(element.get("text-anchor") or "").lower()
                    baseline = str(element.get("dominant-baseline") or "").lower()
                    if anchor == "middle":
                        x -= text_width / 2
                    elif anchor == "end":
                        x -= text_width
                    if baseline in {"middle", "central"}:
                        y -= text_height / 2
                    draw.text(
                        (x, y),
                        text[:40],
                        fill=fill or stroke or "#111827",
                        font=font,
                    )
            for child in list(element):
                if _svg_local_name(child.tag) not in {"defs", "title", "desc"}:
                    walk(child)

        walk(root)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(png_path, format="PNG")

    def _pop_pending_outputs(self, session_id: str) -> list[dict]:
        try:
            session = self.repository.load_session(session_id)
            pending = list((session.scene or {}).get("_pending_outputs") or [])
            if not pending:
                return []
            session.scene["_pending_outputs"] = []
            self.repository.save_session(session)
            return pending
        except Exception as exc:
            self.plugin_logger.warning("pending_outputs_pop_failed session=%s error=%s", session_id, exc)
            return []

    def _duplicate_reply(self, session_id: str, sender_id: str, routed_message: str) -> str:
        now = monotonic()
        expire_after = self.DEDUP_WINDOW_SECONDS * 3
        for key, seen_at in list(self._recent_dm_messages.items()):
            if now - seen_at > expire_after:
                self._recent_dm_messages.pop(key, None)
        normalized = self._dedupe_text(routed_message)
        if not normalized:
            return ""
        key = (session_id, sender_id, normalized)
        last_seen = self._recent_dm_messages.get(key)
        self._recent_dm_messages[key] = now
        if last_seen is not None and now - last_seen <= self.DEDUP_WINDOW_SECONDS:
            return "这句刚才已经进入结算，我不会重复处理。要改动作，请换一句新的 /dm 意图。"
        return ""

    @staticmethod
    def _dedupe_text(text: str) -> str:
        return " ".join(str(text or "").strip().split())

    @staticmethod
    def _friendly_error_message(exc: Exception) -> str:
        text = str(exc)
        lowered = text.lower()
        if "quota" in lowered or "rate limit" in lowered or "429" in lowered or "额度" in text:
            return "DM 这边的模型额度/频率被打满了，这轮没有写入新结果。稍等一会儿后重发刚才那句。"
        if "badrequest" in lowered or "invalid_request" in lowered or "400" in lowered:
            return "DM 这轮请求被模型接口拒绝了，当前存档未改动。请把刚才的动作换个更短、更明确的说法再发一次。"
        return "DM 内核这轮没跑完，当前存档未改动。请稍后重试，或把动作说得更短一点。"

    def _config_list(self, key: str) -> List[str]:
        if not self.config:
            return []
        try:
            value = self.config.get(key, [])
        except AttributeError:
            value = getattr(self.config, key, [])
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _config_bool(self, key: str, default: bool) -> bool:
        if not self.config:
            return default
        try:
            value = self.config.get(key, default)
        except AttributeError:
            value = getattr(self.config, key, default)
        return bool(value)


def _svg_local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1] if "}" in str(tag) else str(tag)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entity_label(session, entity_id: str, entities: dict) -> str:
    entity = dict(entities.get(entity_id, {}))
    if entity.get("name"):
        return str(entity["name"])
    character = session.characters.get(entity_id)
    if character:
        return character.name or character.id
    return entity_id


def _entity_owner(session, entity_id: str, entities: dict) -> str:
    entity = dict(entities.get(entity_id, {}))
    tags = dict(entity.get("tags", {}))
    if tags.get("player_id"):
        return str(tags["player_id"])
    character_id = str(tags.get("character_id", "") or entity_id)
    character = session.characters.get(character_id)
    if character and character.player_id:
        return str(character.player_id)
    for player_id, bound_id in session.player_character_map.items():
        if bound_id == character_id or bound_id == entity_id:
            return str(player_id)
    return ""


def _font_candidates(svg_path: Path, bold: bool = False) -> list[Path]:
    data_dir = svg_path.parent.parent
    names = (
        ["NotoSansCJKsc-Bold.otf", "NotoSansSC-Bold.otf", "SourceHanSansSC-Bold.otf"]
        if bold
        else ["NotoSansCJKsc-Regular.otf", "NotoSansSC-Regular.otf", "SourceHanSansSC-Regular.otf"]
    )
    candidates: list[Path] = []
    for name in names:
        candidates.append(data_dir / "fonts" / name)
    candidates.extend(
        [
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf"),
            Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
            Path("/usr/share/fonts/truetype/arphic/uming.ttc"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
        ]
    )
    return candidates


def _svg_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def _svg_int(value: object, default: int = 0) -> int:
    return int(round(_svg_float(value, float(default))))


def _svg_points(value: object) -> list[tuple[float, float]]:
    numbers = [float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", str(value or ""))]
    return [(numbers[index], numbers[index + 1]) for index in range(0, len(numbers) - 1, 2)]


def _format_roll_record(record: object) -> str:
    if not isinstance(record, dict):
        return _compact_text(record, 80)
    expression = _compact_text(record.get("expression") or "roll", 40)
    rolls = record.get("rolls") or []
    modifier = int(record.get("modifier") or 0)
    total = record.get("total")
    roll_text = ",".join(str(item) for item in list(rolls)[:20])
    if len(rolls) > 20:
        roll_text += ",..."
    if modifier > 0:
        mod_text = f"+{modifier}"
    elif modifier < 0:
        mod_text = str(modifier)
    else:
        mod_text = ""
    return f"{expression}=[{roll_text}]{mod_text} => {total}"


def _compact_result(value: object) -> str:
    if value is None:
        return "规则执行完成"
    if isinstance(value, dict):
        preferred = []
        for key in ("total", "success", "degree", "outcome", "damage", "result", "message"):
            if key in value:
                preferred.append(f"{key}={value[key]}")
        if preferred:
            return _compact_text("；".join(preferred), 180)
    return _compact_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), 180)


def _compact_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"

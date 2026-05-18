from __future__ import annotations

import re
from typing import Any

from .models import GameSession


UNTITLED_SESSION_TITLE = "未命名团"


def session_display_title(session: GameSession) -> str:
    title = _compact_text(getattr(session, "title", ""), 64)
    if title and title != UNTITLED_SESSION_TITLE:
        return title

    world_tags = session.world_tags if isinstance(session.world_tags, dict) else {}
    scene = session.scene if isinstance(session.scene, dict) else {}
    campaign_generation = world_tags.get("campaign_generation")
    if isinstance(campaign_generation, dict):
        for key in ("generated_title", "title", "template_title"):
            candidate = _title_candidate(campaign_generation.get(key))
            if candidate:
                return candidate
    campaign_contract = world_tags.get("campaign_contract")
    if isinstance(campaign_contract, dict):
        for key in ("title", "template_title", "genre"):
            candidate = _title_candidate(campaign_contract.get(key))
            if candidate:
                return candidate
    for key in ("title", "_title"):
        candidate = _title_candidate(scene.get(key))
        if candidate:
            return candidate
    for key in ("current_objective", "_initial_hook", "summary", "_opening_intro"):
        candidate = derive_session_title_from_text(scene.get(key))
        if candidate:
            return candidate
    for key in ("starting_premise", "campaign_background", "genre"):
        candidate = derive_session_title_from_text(world_tags.get(key))
        if candidate:
            return candidate
    return UNTITLED_SESSION_TITLE


def ensure_session_title(session: GameSession, *sources: Any) -> str:
    current = _compact_text(getattr(session, "title", ""), 64)
    if current and current != UNTITLED_SESSION_TITLE:
        return current

    for source in sources:
        title = _title_from_source(source)
        if title:
            session.title = title
            return title

    title = session_display_title(session)
    if title and title != UNTITLED_SESSION_TITLE:
        session.title = title
    return session.title


def derive_session_title_from_text(value: Any) -> str:
    text = _compact_text(value, 120)
    if not text:
        return ""
    cleaned = re.sub(r"^(当前目标|目标|任务|开场|摘要|背景|剧本|团名)\s*[:：]\s*", "", text).strip()
    sentence = re.split(r"[。！？!?；;]\s*", cleaned, maxsplit=1)[0].strip()
    sentence = re.sub(r"^(?:找到|调查|进入|抵达|前往|保护|护送|清剿|摧毁|夺回)\s*", "", sentence)
    sentence = re.sub(r"^(你们|你的小队|小队|队伍|玩家们|众人)\s*", "", sentence)
    sentence = sentence.strip(" ，,。.!！?？;；")
    if not sentence:
        return ""
    if len(sentence) > 18:
        sentence = sentence[:18].rstrip()
    return sentence or ""


def _title_from_source(source: Any) -> str:
    if isinstance(source, dict):
        for key in ("title", "_title", "generated_title", "template_title", "name"):
            candidate = _title_candidate(source.get(key))
            if candidate:
                return candidate
        for key in ("current_objective", "initial_hook", "_initial_hook", "summary", "_opening_intro"):
            candidate = derive_session_title_from_text(source.get(key))
            if candidate:
                return candidate
        return ""
    return derive_session_title_from_text(source)


def _title_candidate(value: Any) -> str:
    text = _compact_text(value, 64)
    if not text or text == UNTITLED_SESSION_TITLE:
        return ""
    return text


def _compact_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."

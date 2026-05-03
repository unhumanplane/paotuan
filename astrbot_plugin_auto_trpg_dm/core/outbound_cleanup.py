from __future__ import annotations

import re
from dataclasses import dataclass


OPEN_WORLD_REMINDER = "直接说出角色想尝试的行动即可；我会按方式、风险和后果继续裁定。"
HELP_REQUEST_REMINDER = "可以先从可见线索、当前风险和角色目标入手，再直接说出想尝试的做法。"
TARGET_CLARIFICATION_REMINDER = "请直接说明具体目标；若已有上一个明确目标才会沿用，否则需要你补充目标。"
OBSERVATION_REMINDER = "这里有些不寻常的细节；直接描述角色想怎样查看或处理即可。"


@dataclass(frozen=True)
class SemanticReviewCandidate:
    text: str
    start: int
    end: int
    signals: tuple[str, ...] = ()
    reason: str = ""
    player_wants_help: bool = False


@dataclass(frozen=True)
class OutboundCleanupResult:
    text: str
    changed: bool
    reason: str = ""
    removed_blocks: int = 0
    replacement_used: bool = False
    original_chars: int = 0
    cleaned_chars: int = 0
    semantic_candidate: SemanticReviewCandidate | None = None


MENU_INTRO_RE = re.compile(
    r"(你可以(?:选择|选|做|考虑|尝试)|"
    r"你有(?:几|两|三|\d+).{0,8}(?:条路|个选择|种选择|个选项|种选项)|"
    r"有(?:几|两|三|\d+).{0,8}(?:个选择|种选择|条路|个选项|种选项)|"
    r"你面前的选择|你需要决定下一步|需要选择|你现在要怎么办|你选择[：:]|"
    r"接下来(?:你)?可以|下一步(?:你)?可以|下一步(?:你想做什么|[，,、]?\s*你怎么打算)?[：:？?]?|"
    r"要做什么|请选择|你想怎么做|你打算怎么(?:做|处理|应对))"
)
OPTION_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*{1,2}\s*)?"
    r"(?:(?:\d{1,2}|[A-Ca-c]|[一二三四五六七八九十])[、.．)）：:]\s*|[①②③④⑤⑥⑦⑧⑨⑩]\s*)"
)
INLINE_OPTION_RE = re.compile(r"(?:^|[\s，,；;：:])(?:\d{1,2}|[A-Ca-c]|[①②③④⑤⑥⑦⑧⑨⑩])[、.．)）]?")
QUESTION_MENU_RE = re.compile(r"(?:是[^？?]{1,60}[？?].{0,80}(?:还是|或是|或者).{1,80}[？?])")
FOLLOWING_ACTION_MENU_PROMPT_RE = re.compile(
    r"^\s*(?:下一步(?:你想做什么|[，,、]?\s*你怎么打算)?\s*[：:？?]|"
    r"你现在要怎么办\s*[：:？?]|"
    r"你(?:想|打算)怎么(?:做|处理|应对).*[？?])\s*$"
)
SEMANTIC_REVIEW_MAX_CANDIDATE_CHARS = 500
SEMANTIC_REVIEW_START_TERMS = (
    "你是指",
    "你是要",
    "你是想",
    "你可以先告诉我",
    "可以先告诉我",
    "你想要",
    "你打算",
    "下一步",
)
SEMANTIC_REVIEW_CONNECTORS = ("还是", "或者", "或是", "或者同时", "或同时")

HELP_REQUEST_TERMS = (
    "我能做什么",
    "现在能做什么",
    "下一步怎么办",
    "给我提示",
    "给点提示",
    "给点建议",
    "有什么选择",
    "有什么选项",
    "有什么目标",
    "可见目标",
    "我能攻击谁",
    "能攻击谁",
    "攻击谁",
    "攻击什么",
    "能打谁",
    "我卡住了",
    "帮我想",
    "what can i do",
    "hint",
    "options",
    "suggest",
)
FACTUAL_REQUEST_TERMS = (
    "status",
    "token",
    "tokens",
    "debug",
    "audit",
    "日志",
    "规则",
    "规则列表",
    "骰",
    "行动顺序",
    "战斗顺序",
    "轮动顺序",
    "轮到谁",
    "当前轮次",
    "当前回合",
    "玩家列表",
    "角色列表",
    "地图",
)
FACTUAL_LINE_MARKERS = (
    "骰子检定",
    "掷骰",
    "规则：",
    "规则:",
    "结果：",
    "结果:",
    "行动顺序",
    "战斗顺序",
    "轮动顺序",
    "玩家登记",
    "玩家列表",
    "角色列表",
    "token",
    "tokens",
    "上下文",
    "audit",
    "prompt",
    "tool schema",
    "diagnostic",
    "阶段：",
    "阶段:",
    "建议行动/超时锚点",
    "持有人",
    "已行动",
    "未行动",
    "来源",
    "状态",
    "hp",
    "ac",
    "dc",
    "total",
)
ACTION_TERMS = (
    "调查",
    "搜索",
    "搜查",
    "观察",
    "侦查",
    "查看",
    "检查",
    "回忆",
    "找",
    "偷听",
    "敲",
    "开门",
    "打开",
    "破门",
    "强闯",
    "进入",
    "靠近",
    "接近",
    "离开",
    "绕开",
    "绕路",
    "攻击",
    "防御",
    "移动",
    "询问",
    "交涉",
    "谈判",
    "威胁",
    "说服",
    "等待",
    "撤退",
    "逃跑",
    "推进",
    "准备",
    "修整",
    "换弹",
    "深入",
    "踏入",
    "清理",
    "扫描",
    "切换",
    "拔",
    "拉开",
    "补",
    "压制",
    "喷吐",
    "速射",
    "强攻",
    "突入",
    "投",
    "评估",
    "调整",
    "重新评估",
    "躲藏",
    "躲避",
    "潜行",
    "跟踪",
    "追踪",
    "喊",
    "呼救",
    "使用",
    "拿",
    "捡",
    "攀爬",
    "跳",
    "施法",
    "治疗",
)
ACTION_LINE_PREFIXES = (
    "继续",
    "直接",
    "先",
    "试着",
    "尝试",
    "转身",
    "悄悄",
    "短暂",
    "趁",
    "快速",
    "沿着",
    "给",
    "用",
    "拔",
    "切换",
    "退后",
    "换条",
)


def cleanup_menu_like_guidance(
    text: str,
    player_message: str = "",
    *,
    diagnostic: bool = False,
) -> OutboundCleanupResult:
    original = str(text or "")
    if not original.strip() or diagnostic:
        return _unchanged(original)

    player_wants_help = _player_wants_help(player_message)
    lines = original.splitlines()
    new_lines: list[str] = []
    changed = False
    removed_blocks = 0
    replacement_used = False
    reason = ""
    index = 0

    while index < len(lines):
        line = lines[index]
        if _line_should_be_preserved(line):
            new_lines.append(line)
            index += 1
            continue

        if _line_starts_bare_action_menu(lines, index):
            end = _bare_action_menu_end(lines, index)
            if player_wants_help:
                new_lines.append(HELP_REQUEST_REMINDER)
                replacement_used = True
                reason = reason or "explicit_help_numbered_menu_softened"
            else:
                reason = reason or "bare_action_menu_guidance_removed"
            changed = True
            removed_blocks += 1
            index = end
            continue

        if _line_starts_following_action_menu(lines, index):
            end = _following_action_menu_end(lines, index)
            if player_wants_help:
                new_lines.append(HELP_REQUEST_REMINDER)
                replacement_used = True
                reason = reason or "explicit_help_hidden_menu_softened"
            else:
                reason = reason or "hidden_menu_like_action_guidance_removed"
            changed = True
            removed_blocks += 1
            index = end
            continue

        menu_index = _menu_intro_index(line)
        if menu_index >= 0 and _line_starts_menu_block(lines, index):
            end = _menu_block_end(lines, index)
            block = lines[index:end]
            clue_menu = _block_looks_like_factual_clue_list(block)
            visible_target_list = player_wants_help and _block_looks_like_visible_target_list(block)
            if _block_is_allowed_setup_or_confirmation(block, player_message):
                new_lines.extend(block)
                index = end
                continue
            if visible_target_list:
                new_lines.extend(block)
                index = end
                continue
            if not clue_menu and _block_should_be_preserved(block):
                new_lines.append(line)
                index += 1
                continue

            prefix = _clean_menu_prefix(line[:menu_index])
            if prefix:
                new_lines.append(prefix)
            if player_wants_help and _count_option_lines(block) >= 2:
                new_lines.append(HELP_REQUEST_REMINDER)
                replacement_used = True
                reason = reason or "explicit_help_numbered_menu_softened"
            elif clue_menu:
                reason = reason or "factual_clue_menu_removed"
            else:
                reason = reason or "menu_like_action_guidance_removed"
            changed = True
            removed_blocks += 1
            index = end
            continue

        if _looks_like_non_numbered_action_suggestion(line, player_wants_help):
            changed = True
            removed_blocks += 1
            reason = reason or "menu_like_action_guidance_removed"
            index += 1
            continue

        hidden_index = _hidden_menu_start_index(line)
        if hidden_index >= 0:
            if player_wants_help and _looks_like_visible_target_choice_text(line[hidden_index:]):
                new_lines.append(line)
                index += 1
                continue
            prefix = _clean_menu_prefix(line[:hidden_index])
            if prefix:
                new_lines.append(prefix)
            if player_wants_help:
                new_lines.append(HELP_REQUEST_REMINDER)
                replacement_used = True
                reason = reason or "explicit_help_hidden_menu_softened"
            elif _looks_like_closed_clarification_menu(line[hidden_index:]):
                reason = reason or "closed_clarification_menu_removed"
            else:
                reason = reason or "hidden_menu_like_action_guidance_removed"
            changed = True
            removed_blocks += 1
            index += 1
            continue

        new_lines.append(line)
        index += 1

    if not changed:
        return _unchanged(
            original,
            semantic_candidate=_find_semantic_review_candidate(
                original,
                player_message=player_message,
                player_wants_help=player_wants_help,
            ),
        )

    cleaned = _collapse_blank_lines("\n".join(new_lines)).strip()
    if not cleaned:
        if reason == "closed_clarification_menu_removed":
            cleaned = TARGET_CLARIFICATION_REMINDER
        elif reason == "factual_clue_menu_removed":
            cleaned = OBSERVATION_REMINDER
        else:
            cleaned = OPEN_WORLD_REMINDER
        replacement_used = True
        reason = reason or "menu_only_reply_replaced"
    return OutboundCleanupResult(
        text=cleaned,
        changed=cleaned != original,
        reason=reason,
        removed_blocks=removed_blocks,
        replacement_used=replacement_used,
        original_chars=len(original),
        cleaned_chars=len(cleaned),
    )


def _unchanged(
    text: str,
    *,
    semantic_candidate: SemanticReviewCandidate | None = None,
) -> OutboundCleanupResult:
    return OutboundCleanupResult(
        text=text,
        changed=False,
        original_chars=len(text),
        cleaned_chars=len(text),
        semantic_candidate=semantic_candidate,
    )


def apply_semantic_menu_judgment(
    text: str,
    candidate: SemanticReviewCandidate | None,
    action: str,
) -> OutboundCleanupResult:
    original = str(text or "")
    if candidate is None or action == "keep":
        return _unchanged(original)
    if action not in {"delete_candidate", "replace_with_local_help"}:
        return _unchanged(original)
    if candidate.start < 0 or candidate.end > len(original) or candidate.start >= candidate.end:
        return _unchanged(original)

    replacement = HELP_REQUEST_REMINDER if action == "replace_with_local_help" else ""
    cleaned = _replace_span(original, candidate.start, candidate.end, replacement).strip()
    replacement_used = action == "replace_with_local_help"
    reason = "semantic_tail_menu_replaced" if replacement_used else "semantic_tail_menu_deleted"
    if not cleaned:
        cleaned = OPEN_WORLD_REMINDER
        replacement_used = True
        reason = "semantic_menu_only_reply_replaced"
    return OutboundCleanupResult(
        text=cleaned,
        changed=cleaned != original,
        reason=reason,
        removed_blocks=1,
        replacement_used=replacement_used,
        original_chars=len(original),
        cleaned_chars=len(cleaned),
    )


def _player_wants_help(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text or any(term in text for term in FACTUAL_REQUEST_TERMS):
        return False
    return any(term in text for term in HELP_REQUEST_TERMS)


def _find_semantic_review_candidate(
    text: str,
    *,
    player_message: str = "",
    player_wants_help: bool = False,
) -> SemanticReviewCandidate | None:
    original = str(text or "")
    if not original.strip() or _player_wants_factual_output(player_message):
        return None
    paragraphs = _paragraph_spans(original)
    if not paragraphs:
        return None
    latter_half_start = int(len(original) * 0.55)
    tail_paragraph_count = 2 if len(paragraphs) >= 3 else 1
    first_tail_index = max(0, len(paragraphs) - tail_paragraph_count)
    for index in range(len(paragraphs) - 1, -1, -1):
        start, end, paragraph = paragraphs[index]
        if index < first_tail_index and start < latter_half_start:
            continue
        candidate = _semantic_candidate_from_paragraph(
            paragraph,
            paragraph_start=start,
            paragraph_end=end,
            player_wants_help=player_wants_help,
        )
        if candidate and _semantic_candidate_is_scoped_to_tail(original, candidate):
            return candidate
    return None


def _player_wants_factual_output(message: str) -> bool:
    text = str(message or "").strip().lower()
    return bool(text and any(term in text for term in FACTUAL_REQUEST_TERMS))


def _paragraph_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", text):
        chunk = match.group(0)
        stripped = chunk.strip()
        if not stripped:
            continue
        leading = len(chunk) - len(chunk.lstrip())
        trailing = len(chunk) - len(chunk.rstrip())
        start = match.start() + leading
        end = match.end() - trailing
        spans.append((start, end, text[start:end]))
    return spans


def _semantic_candidate_from_paragraph(
    paragraph: str,
    *,
    paragraph_start: int,
    paragraph_end: int,
    player_wants_help: bool,
) -> SemanticReviewCandidate | None:
    text = str(paragraph or "").strip()
    if not text:
        return None
    if _paragraph_should_be_preserved(text):
        return None
    signals = _semantic_review_signals(text)
    if not signals:
        return None
    question_count = text.count("？") + text.count("?")
    has_connector = any(connector in text for connector in SEMANTIC_REVIEW_CONNECTORS)
    has_start = any(term in text for term in SEMANTIC_REVIEW_START_TERMS)
    if not has_start or not (has_connector or question_count >= 2):
        return None
    local_start = _semantic_candidate_local_start(text)
    if local_start < 0:
        return None
    raw_candidate = text[local_start:]
    leading = len(raw_candidate) - len(raw_candidate.lstrip())
    candidate_text = raw_candidate.strip()
    if not candidate_text or len(candidate_text) > SEMANTIC_REVIEW_MAX_CANDIDATE_CHARS:
        return None
    return SemanticReviewCandidate(
        text=candidate_text,
        start=paragraph_start + local_start + leading,
        end=paragraph_end,
        signals=tuple(signals),
        reason="ambiguous_tail_choice_candidate",
        player_wants_help=player_wants_help,
    )


def _paragraph_should_be_preserved(paragraph: str) -> bool:
    lines = str(paragraph or "").splitlines()
    if any(_line_should_be_preserved(line) for line in lines):
        return True
    return _block_should_be_preserved(lines)


def _semantic_candidate_is_scoped_to_tail(text: str, candidate: SemanticReviewCandidate) -> bool:
    before = str(text or "")[: candidate.start].strip()
    after = str(text or "")[candidate.end :].strip()
    if not before and not after:
        return False
    return True


def _semantic_review_signals(text: str) -> list[str]:
    signals: list[str] = []
    for term in (*SEMANTIC_REVIEW_START_TERMS, *SEMANTIC_REVIEW_CONNECTORS):
        if term in text and term not in signals:
            signals.append(term)
    if text.count("？") + text.count("?") >= 2:
        signals.append("multiple_questions")
    return signals


def _semantic_candidate_local_start(text: str) -> int:
    starts = [text.find(term) for term in SEMANTIC_REVIEW_START_TERMS if text.find(term) >= 0]
    if starts:
        return min(starts)
    connector_starts = [text.find(term) for term in SEMANTIC_REVIEW_CONNECTORS if text.find(term) >= 0]
    if connector_starts:
        return max(0, _sentence_start_before(text, min(connector_starts)))
    return -1


def _sentence_start_before(text: str, index: int) -> int:
    start = 0
    for mark in ("。", "！", "!", "？", "?", "\n"):
        position = text.rfind(mark, 0, index)
        if position >= 0:
            start = max(start, position + 1)
    return start


def _replace_span(text: str, start: int, end: int, replacement: str) -> str:
    prefix = text[:start].rstrip()
    suffix = text[end:].lstrip()
    parts = [part for part in (prefix, replacement.strip(), suffix) if part]
    return _collapse_blank_lines("\n".join(parts))


def _menu_intro_index(line: str) -> int:
    match = MENU_INTRO_RE.search(str(line or ""))
    return match.start() if match else -1


def _line_starts_menu_block(lines: list[str], index: int) -> bool:
    line = lines[index]
    if _looks_like_open_world_reminder(line):
        return False
    if _looks_like_hidden_action_menu(line):
        return True
    if _line_has_inline_numbered_options(line):
        return True
    if _count_following_option_lines(lines, index + 1) >= 2:
        return True
    if _count_following_action_candidate_lines(lines, index + 1) >= 2:
        return True
    return _looks_like_inline_action_menu(line)


def _menu_block_end(lines: list[str], start: int) -> int:
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip():
            if _next_nonempty_is_option(lines, end + 1) or _next_nonempty_is_action_candidate(
                lines, end + 1
            ) or _next_nonempty_is_menu_closing_prompt(lines, end + 1):
                end += 1
                continue
            break
        if _is_option_line(line) or _is_action_candidate_line(line) or _is_menu_closing_prompt(line):
            end += 1
            continue
        break
    return end


def _line_starts_bare_action_menu(lines: list[str], index: int) -> bool:
    if not _is_option_line(lines[index]):
        return False
    return _count_following_action_option_lines(lines, index) >= 2


def _bare_action_menu_end(lines: list[str], start: int) -> int:
    end = start
    while end < len(lines):
        line = lines[end]
        if not line.strip():
            if _next_nonempty_is_action_option(lines, end + 1) or _next_nonempty_is_menu_closing_prompt(
                lines, end + 1
            ):
                end += 1
                continue
            break
        if _is_action_option_line(line) or _is_menu_closing_prompt(line):
            end += 1
            continue
        break
    return end


def _line_starts_following_action_menu(lines: list[str], index: int) -> bool:
    line = _plain_line_text(lines[index])
    if _looks_like_open_world_reminder(line) or _line_should_be_preserved(line):
        return False
    if not FOLLOWING_ACTION_MENU_PROMPT_RE.search(line):
        return False
    return _count_following_action_candidate_lines(lines, index + 1) >= 2


def _following_action_menu_end(lines: list[str], start: int) -> int:
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip():
            if _next_nonempty_is_action_candidate(
                lines, end + 1
            ) or _next_nonempty_is_menu_closing_prompt(lines, end + 1):
                end += 1
                continue
            break
        if _is_action_candidate_line(line) or _is_menu_closing_prompt(line):
            end += 1
            continue
        break
    return end


def _count_following_action_candidate_lines(lines: list[str], start: int, limit: int = 6) -> int:
    count = 0
    scanned = 0
    for line in lines[start:]:
        if scanned >= limit:
            break
        scanned += 1
        if not line.strip():
            continue
        if _is_action_candidate_line(line):
            count += 1
            continue
        break
    return count


def _next_nonempty_is_action_candidate(lines: list[str], start: int) -> bool:
    for line in lines[start:]:
        if not line.strip():
            continue
        return _is_action_candidate_line(line)
    return False


def _next_nonempty_is_option(lines: list[str], start: int) -> bool:
    for line in lines[start:]:
        if not line.strip():
            continue
        return _is_option_line(line)
    return False


def _next_nonempty_is_menu_closing_prompt(lines: list[str], start: int) -> bool:
    for line in lines[start:]:
        if not line.strip():
            continue
        return _is_menu_closing_prompt(line)
    return False


def _next_nonempty_is_action_option(lines: list[str], start: int) -> bool:
    for line in lines[start:]:
        if not line.strip():
            continue
        return _is_action_option_line(line)
    return False


def _count_following_action_option_lines(lines: list[str], start: int, limit: int = 8) -> int:
    count = 0
    scanned = 0
    for line in lines[start:]:
        if scanned >= limit:
            break
        scanned += 1
        if not line.strip():
            continue
        if _is_action_option_line(line):
            count += 1
            continue
        break
    return count


def _count_following_option_lines(lines: list[str], start: int, limit: int = 8) -> int:
    count = 0
    scanned = 0
    for line in lines[start:]:
        if scanned >= limit:
            break
        scanned += 1
        if not line.strip():
            continue
        if _is_option_line(line):
            count += 1
            continue
        break
    return count


def _count_option_lines(lines: list[str]) -> int:
    return sum(1 for line in lines if _is_option_line(line))


def _is_option_line(line: str) -> bool:
    return bool(OPTION_LINE_RE.search(_plain_line_text(line)))


def _is_action_option_line(line: str) -> bool:
    return _is_option_line(line) and _is_action_candidate_text(line)


def _line_has_inline_numbered_options(line: str) -> bool:
    return len(INLINE_OPTION_RE.findall(str(line or ""))) >= 2


def _looks_like_inline_action_menu(line: str) -> bool:
    text = _plain_line_text(line)
    if _looks_like_open_world_reminder(text):
        return False
    if "你可以" not in text and "接下来" not in text and "下一步" not in text and "选择" not in text:
        return False
    if any(term in text for term in ("选择", "选项", "路线", "条路")) and any(
        sep in text for sep in ("、", "或者", "或是", "，", ",")
    ):
        return True
    action_hits = sum(1 for term in ACTION_TERMS if term in text)
    return action_hits >= 2 and any(sep in text for sep in ("、", "或者", "或是", "，", ","))


def _looks_like_hidden_action_menu(line: str) -> bool:
    return _hidden_menu_start_index(line) >= 0


def _is_action_candidate_line(line: str) -> bool:
    return _is_action_candidate_text(line)


def _is_action_candidate_text(value: str) -> bool:
    text = _plain_line_text(value)
    text = _strip_option_marker(text)
    text = re.sub(r"^\s*[-*]\s*", "", text).strip()
    text = re.sub(r"^[^\w\u4e00-\u9fff]+", "", text).strip()
    text = re.sub(r"^(?:是|还是|或是|或者|或)\s*", "", text)
    text = re.split(r"——|--| - |：|:", text, maxsplit=1)[0].strip()
    text = text.strip("。！？!?；;，, ")
    if not text or len(text) > 72:
        return False
    if _line_should_be_preserved(text) or _is_option_line(text):
        return False
    if any(text.startswith(prefix) for prefix in ACTION_LINE_PREFIXES):
        return any(term in text[:32] for term in ACTION_TERMS)
    return any(text.startswith(term) for term in ACTION_TERMS)


def _hidden_menu_start_index(line: str) -> int:
    text = " ".join(_plain_line_text(line).strip().split())
    if not text or _looks_like_open_world_reminder(text) or _line_should_be_preserved(text):
        return -1
    clarification_match = re.search(r"你是(?:指|要|想).{0,120}(?:还是|或是|或者).{1,120}[？?]", text)
    if clarification_match and _sentence_like_action_count(clarification_match.group(0)) >= 2:
        return clarification_match.start()
    question_match = QUESTION_MENU_RE.search(text)
    if question_match and _sentence_like_action_count(question_match.group(0)) >= 2:
        return question_match.start()
    question_count = text.count("？") + text.count("?")
    framed_match = re.search(r"你打算怎么(?:做|处理|应对)", text)
    if framed_match and "还是" in text and question_count >= 1 and _sentence_like_action_count(text) >= 2:
        return framed_match.start()
    soft_framed_match = re.search(r"你想怎么做", text)
    if soft_framed_match and any(connector in text for connector in ("还是", "或者", "或是")):
        if question_count >= 1 and _sentence_like_action_count(text) >= 2:
            return soft_framed_match.start()
    next_match = re.search(r"下一步\s*[？?]", text)
    if next_match and _sentence_like_action_count(text) >= 2:
        return next_match.start()
    return -1


def _sentence_like_action_count(text: str) -> int:
    parts = [part.strip() for part in re.split(r"[。！？!?；;，,]", text) if part.strip()]
    return sum(1 for part in parts if _is_action_candidate_text(part))


def _looks_like_non_numbered_action_suggestion(line: str, player_wants_help: bool) -> bool:
    if player_wants_help:
        return False
    text = _plain_line_text(line).strip()
    if not text or _looks_like_open_world_reminder(text) or _line_should_be_preserved(text):
        return False
    if "你可以" not in text and "接下来" not in text and "下一步" not in text:
        return False
    menu_terms = ("选择", "选项", "路线", "条路", "告诉我", "让我", "行动")
    return any(term in text for term in menu_terms) and (
        _looks_like_inline_action_menu(text) or any(term in text for term in ("选择", "选项", "路线", "条路"))
    )


def _looks_like_open_world_reminder(line: str) -> bool:
    text = str(line or "")
    return "后果" in text and "风险" in text and any(term in text for term in ("直接", "自由", "想尝试"))


def _line_should_be_preserved(line: str) -> bool:
    text = _plain_line_text(line).strip().lower()
    if not text:
        return False
    return any(marker in text for marker in FACTUAL_LINE_MARKERS)


def _block_should_be_preserved(lines: list[str]) -> bool:
    joined = "\n".join(lines).strip().lower()
    if not joined:
        return False
    factual_hits = sum(1 for marker in FACTUAL_LINE_MARKERS if marker in joined)
    return factual_hits >= 2 and "你可以选择" not in joined and "请选择" not in joined


def _looks_like_closed_clarification_menu(text: str) -> bool:
    source = str(text or "").strip()
    return bool(re.search(r"^你是(?:指|要|想)", source)) and any(
        connector in source for connector in ("还是", "或者", "或是")
    )


def _block_looks_like_factual_clue_list(lines: list[str]) -> bool:
    if not lines:
        return False
    intro = str(lines[0] or "")
    clue_terms = ("关注", "线索", "可感知", "看见", "听见", "闻到", "注意到", "记下", "客观")
    if not any(term in intro for term in clue_terms):
        return False
    option_texts = [_option_line_text(line) for line in lines[1:] if _is_option_line(line)]
    if len(option_texts) < 2:
        return False
    return not any(_is_action_candidate_text(text) for text in option_texts)


def _block_looks_like_visible_target_list(lines: list[str]) -> bool:
    if not lines:
        return False
    intro = str(lines[0] or "")
    if not any(term in intro for term in ("目标", "攻击谁", "攻击目标", "可见", "看得见", "能攻击", "可攻击")):
        return False
    option_texts = [_option_line_text(line) for line in lines[1:] if _is_option_line(line)]
    if len(option_texts) < 2:
        return False
    return all(_looks_like_visible_target_text(text) for text in option_texts)


def _block_is_allowed_setup_or_confirmation(lines: list[str], player_message: str) -> bool:
    if not lines:
        return False
    player_text = str(player_message or "")
    joined = "\n".join(str(line or "") for line in lines)
    if _player_message_requests_story_setup(player_text):
        return True
    if _player_message_requests_character_setup(player_text):
        return True
    if _player_message_needs_intent_clarification(player_text, joined):
        return True
    if _player_message_requests_meta_confirmation(player_text, joined):
        return True
    return False


def _player_message_requests_story_setup(text: str) -> bool:
    return any(term in text for term in ("开团", "新团", "开新故事", "新故事", "世界观", "战役"))


def _player_message_requests_character_setup(text: str) -> bool:
    return any(term in text for term in ("建卡", "创建角色", "角色设定", "绑定", "机械贤者"))


def _player_message_needs_intent_clarification(player_text: str, reply_text: str) -> bool:
    return any(term in reply_text for term in ("不太像角色行动意图", "你想做什么", "不太明白你的意思"))


def _player_message_requests_meta_confirmation(player_text: str, reply_text: str) -> bool:
    meta_terms = (
        "战斗完成",
        "章节",
        "结束",
        "完结",
        "清空",
        "重开",
        "存档",
        "撤退返回",
        "正式结束",
        "二次确认",
    )
    return any(term in player_text for term in meta_terms) or any(term in reply_text for term in meta_terms)


def _looks_like_visible_target_choice_text(text: str) -> bool:
    source = str(text or "")
    if not re.search(r"你是(?:指|要|想)", source):
        return False
    parts = [part.strip(" ？?。！!；;，,") for part in re.split(r"[？?，,；;]|还是|或者|或是", source)]
    targets = [re.sub(r"^你是(?:指|要|想)", "", part).strip() for part in parts if part.strip()]
    return len(targets) >= 2 and all(_looks_like_visible_target_text(target) for target in targets)


def _looks_like_visible_target_text(text: str) -> bool:
    value = re.sub(r"^(?:攻击|打|选择|目标是)\s*", "", str(text or "").strip())
    if not value or len(value) > 36:
        return False
    if _is_action_candidate_text(value):
        return False
    target_terms = (
        "左",
        "右",
        "前",
        "后",
        "守卫",
        "敌人",
        "怪物",
        "门",
        "锁",
        "目标",
        "身影",
        "弩手",
        "法师",
        "持刀",
        "拿灯",
    )
    return any(term in value for term in target_terms)


def _option_line_text(line: str) -> str:
    return _strip_option_marker(_plain_line_text(line)).strip()


def _is_menu_closing_prompt(line: str) -> bool:
    text = _plain_line_text(line).strip()
    return bool(re.search(r"^(?:你要用哪个|你的选择|你选哪个|选哪一个|请选择)\s*[？?。!！]*$", text))


def _plain_line_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"\*{1,3}", "", text)
    text = text.replace("`", "")
    return text.strip()


def _clean_menu_prefix(value: str) -> str:
    return _plain_line_text(value).strip(" \t：:，,；;-—")


def _strip_option_marker(value: str) -> str:
    return OPTION_LINE_RE.sub("", str(value or ""), count=1).strip()


def _collapse_blank_lines(text: str) -> str:
    lines = text.splitlines()
    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        collapsed.append(line.rstrip())
        previous_blank = blank
    return "\n".join(collapsed)

from __future__ import annotations

import random
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..core.map_delivery_cadence import normalize_pending_outputs
from ..core.models import RuleRef, compact_rules
from ..storage.json_repository import JsonGameRepository
from ..rules.python_runtime import PythonRuleRuntime


class RegisterRuleArgs(BaseModel):
    rule_name: str = Field(..., description="规则函数名，使用简短英文或拼音，后续 execute_rule 将按此名称调用")
    description: str = Field(..., description="这条规则解决什么判定，例如近战命中、枪械伤害、恐惧检定")
    code_string: str = Field(
        ...,
        description=(
            "Python 子集代码，必须且只能定义 calculate(**kwargs) 或具名参数的 calculate 函数。"
            "随机数只能使用 roll('1d20')、roll(20)、roll(20, count=2, modifier=3) 或 randint(1, 20)。"
            "可以用 kwargs.get('bonus', 0) 读取默认参数。不要 import random，不要调用 random.*，不要使用 list.append；需要列表时用列表推导式。"
        ),
    )
    input_schema: Dict[str, Any] = Field(default_factory=dict, description="规则入参说明")
    output_schema: Dict[str, Any] = Field(default_factory=dict, description="规则输出说明")
    tags: List[str] = Field(default_factory=list, description="规则标签，如 combat、damage、dice")


class ExecuteRuleArgs(BaseModel):
    rule_name: str = Field(..., description="要执行的已注册规则名称")
    args: Dict[str, Any] = Field(default_factory=dict, description="传给 calculate 的参数字典")
    version: Optional[int] = Field(default=None, description="可选规则版本；为空则使用最新版")
    reason: str = Field(default="", description="为什么进行这次检定/掷骰；不要放进 args，避免影响规则函数入参")

    class Config:
        extra = "forbid"


class ResolveCheckArgs(BaseModel):
    action: str = Field(..., description="Concrete action being attempted.")
    actor_id: str = Field(default="", description="Character id when known, e.g. pc_yaka.")
    actor_name: str = Field(default="", description="Character or NPC name when id is unknown.")
    check_type: str = Field(default="skill", description="skill, ability, tool, social, stealth, perception, knowledge, mechanical, or custom.")
    ability: str = Field(default="", description="Optional ability context.")
    skill: str = Field(default="", description="Optional skill or tool context.")
    dc: Optional[Any] = Field(default=None, description="Numeric DC when known.")
    target_dc: Optional[Any] = Field(default=None, description="Alias for dc.")
    difficulty: Optional[Any] = Field(default="", description="easy/medium/hard/very_hard/extreme, simple/moderate, or numeric DC.")
    bonus: Optional[Any] = Field(default=None, description="Final total modifier; prefer this over separate fields.")
    modifier: Optional[Any] = Field(default=None, description="Alias for final total modifier when bonus is not known.")
    ability_modifier: Optional[Any] = Field(default=None, description="Ability modifier to add when bonus is not supplied.")
    proficiency_bonus: Optional[Any] = Field(default=None, description="Proficiency modifier to add when bonus is not supplied.")
    skill_bonus: Optional[Any] = Field(default=None, description="Skill/tool modifier to add when bonus is not supplied.")
    item_bonus: Optional[Any] = Field(default=None, description="Equipment modifier to add when bonus is not supplied.")
    situational_bonus: Optional[Any] = Field(default=None, description="Situational modifier to add when bonus is not supplied.")
    penalty: Optional[Any] = Field(default=None, description="Penalty to subtract when bonus is not supplied.")
    modifier_note: str = Field(default="", description="Explain bonuses, proficiency, gear, advantage, penalties, or exclusions.")
    advantage: Any = Field(default="normal", description="normal, advantage, disadvantage, true, or false.")
    disadvantage: Any = Field(default=None, description="Set true as an alias for advantage='disadvantage'.")
    stakes: str = Field(default="", description="Success, partial success, and failure stakes.")
    reason: str = Field(default="", description="Why this check is required.")


class ListRulesArgs(BaseModel):
    detail_level: str = Field(default="summary", description="summary 或 detail；summary 返回二级摘要，detail 返回有限数量规则详情")
    tag: str = Field(default="", description="可选标签过滤，例如 combat、damage、dice")
    limit: int = Field(default=16, ge=1, le=64, description="detail 模式最多返回多少条")


class RuleTools:
    def __init__(
        self,
        repository: JsonGameRepository,
        rule_runtime: PythonRuleRuntime,
        session_id: str,
        actor: dict[str, str] | None = None,
        message: str = "",
    ):
        self.repository = repository
        self.rule_runtime = rule_runtime
        self.session_id = session_id
        self.actor = actor or {}
        self.message = message
        self._list_rules_seen: set[tuple[str, str, int]] = set()

    async def register_rule(
        self,
        rule_name: str,
        description: str,
        code_string: str,
        input_schema: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """注册一条由 LLM 生成的 TRPG 纯计算规则，后续可通过 execute_rule 复用。"""
        result = self.rule_runtime.register_rule(
            rule_name=rule_name,
            description=description,
            code_string=code_string,
            input_schema=input_schema or {},
            output_schema=output_schema or {},
            tags=tags or [],
        )
        if result.get("ok"):
            session = self.repository.load_session(self.session_id)
            rule_ref = self.rule_runtime.load_rule_ref(
                result["rule_name"],
                int(result["version"]),
            )
            if rule_ref:
                session.rules[rule_ref.name] = rule_ref
                self.repository.save_session(session)
        elif result.get("error") == "validation_failed":
            result["hint"] = _rule_validation_hint(str(result.get("reason") or ""))
        self.repository.append_audit(
            self.session_id,
            {
                "type": "tool",
                "tool": "register_rule",
                "input": {
                    "rule_name": rule_name,
                    "description": description,
                    "input_schema": input_schema or {},
                    "output_schema": output_schema or {},
                    "tags": tags or [],
                },
                "result": result,
            },
        )
        return result

    async def execute_rule(
        self,
        rule_name: str,
        args: Optional[Dict[str, Any]] = None,
        version: Optional[int] = None,
        reason: str = "",
    ) -> Dict[str, Any]:
        """执行已注册 TRPG 规则，用于检定、伤害、资源消耗和随机判定。"""
        normalized_args = _normalize_execute_rule_args(rule_name, args or {})
        result = self.rule_runtime.execute_rule(
            rule_name=rule_name,
            args=normalized_args,
            version=version,
        )
        effective_args = dict(result.get("coerced_args") or normalized_args)
        _augment_check_result(rule_name=rule_name, args=effective_args, result=result)
        if result.get("rolls"):
            self._queue_dice_check(
                rule_name=rule_name,
                args=effective_args,
                version=version,
                reason=reason,
                result=result,
            )
            result["dice_check_note_queued"] = True
        self.repository.append_audit(
            self.session_id,
            {
                "type": "tool",
                "tool": "execute_rule",
                "input": {
                    "rule_name": rule_name,
                    "args": normalized_args,
                    "version": version,
                    "reason": reason,
                    "raw_args": args or {},
                },
                "result": result,
            },
        )
        return result

    async def resolve_check(
        self,
        action: str = "",
        actor_id: str = "",
        actor_name: str = "",
        check_type: str = "skill",
        ability: str = "",
        skill: str = "",
        dc: Optional[Any] = None,
        target_dc: Optional[Any] = None,
        difficulty: Optional[Any] = "",
        bonus: Optional[Any] = None,
        modifier: Optional[Any] = None,
        ability_modifier: Optional[Any] = None,
        proficiency_bonus: Optional[Any] = None,
        skill_bonus: Optional[Any] = None,
        item_bonus: Optional[Any] = None,
        situational_bonus: Optional[Any] = None,
        penalty: Optional[Any] = None,
        modifier_note: str = "",
        advantage: Any = "normal",
        disadvantage: Any = None,
        stakes: str = "",
        reason: str = "",
        **extra_context: Any,
    ) -> Dict[str, Any]:
        action_text = str(action or "").strip()
        numeric_context = {
            "modifier": modifier,
            "ability_modifier": ability_modifier,
            "proficiency_bonus": proficiency_bonus,
            "skill_bonus": skill_bonus,
            "item_bonus": item_bonus,
            "situational_bonus": situational_bonus,
            "penalty": penalty,
            **extra_context,
        }
        dc_source = dc if dc not in (None, "") else target_dc
        advantage_context = dict(extra_context)
        if disadvantage is not None:
            advantage_context["disadvantage"] = disadvantage
        input_payload = {
            "action": action,
            "actor_id": actor_id,
            "actor_name": actor_name,
            "check_type": check_type,
            "ability": ability,
            "skill": skill,
            "dc": dc,
            "target_dc": target_dc,
            "difficulty": difficulty,
            "bonus": bonus,
            "modifier": modifier,
            "ability_modifier": ability_modifier,
            "proficiency_bonus": proficiency_bonus,
            "skill_bonus": skill_bonus,
            "item_bonus": item_bonus,
            "situational_bonus": situational_bonus,
            "penalty": penalty,
            "modifier_note": modifier_note,
            "advantage": advantage,
            "disadvantage": disadvantage,
            "stakes": stakes,
            "reason": reason,
            "extra_context": extra_context,
        }
        if not action_text:
            result = {
                "ok": False,
                "error": "missing_check_action",
                "message": "resolve_check requires a concrete action.",
            }
            self.repository.append_audit(
                self.session_id,
                {"type": "tool", "tool": "resolve_check", "input": input_payload, "result": result},
            )
            return result

        dc_value, dc_warning = _resolve_check_dc(dc_source, difficulty)
        bonus_value = _resolve_check_bonus(bonus, numeric_context)
        advantage_mode = _normalize_advantage(advantage, advantage_context)
        rolls = _roll_d20_for_advantage(advantage_mode)
        kept_roll = max(rolls) if advantage_mode == "advantage" else min(rolls) if advantage_mode == "disadvantage" else rolls[0]
        total = kept_roll + bonus_value
        margin = total - dc_value
        outcome = _check_outcome(kept_roll=kept_roll, total=total, dc=dc_value)
        success = outcome in {"critical_success", "success"}
        check_id = _new_check_id(actor_id=actor_id, actor_name=actor_name)
        normalized_args = {
            "actor_id": str(actor_id or "").strip(),
            "actor_name": str(actor_name or "").strip(),
            "action": action_text,
            "check_type": str(check_type or "skill").strip() or "skill",
            "ability": str(ability or "").strip(),
            "skill": str(skill or "").strip(),
            "dc": dc_value,
            "bonus": bonus_value,
            "advantage": advantage_mode,
            "modifier_note": str(modifier_note or "").strip(),
            "stakes": str(stakes or "").strip(),
            "reason": str(reason or "").strip(),
        }
        roll_record = {
            "expression": "2d20kh1" if advantage_mode == "advantage" else "2d20kl1" if advantage_mode == "disadvantage" else "1d20",
            "rolls": rolls,
            "modifier": bonus_value,
            "total": total,
        }
        result_payload = {
            "roll": kept_roll,
            "total": total,
            "modifier": bonus_value,
            "dc": dc_value,
            "success": success,
            "outcome": outcome,
            "margin": margin,
        }
        result: Dict[str, Any] = {
            "ok": True,
            "tool": "resolve_check",
            "check_id": check_id,
            "actor_id": normalized_args["actor_id"],
            "actor_name": normalized_args["actor_name"],
            "action": action_text,
            "check_type": normalized_args["check_type"],
            "ability": normalized_args["ability"],
            "skill": normalized_args["skill"],
            "dc": dc_value,
            "bonus": bonus_value,
            "advantage": advantage_mode,
            "rolls": [roll_record],
            "result": result_payload,
            "check": {
                "rule_name": "resolve_check",
                "total": total,
                "dc": dc_value,
                "success": success,
                "margin": margin,
            },
            "state_write_support": True,
            "narrative_guidance": _narrative_guidance_for_outcome(outcome),
            "normalized_args": {
                key: value for key, value in normalized_args.items() if value not in ("", None)
            },
        }
        if dc_warning:
            result["warnings"] = [dc_warning]
        if extra_context:
            result["extra_context"] = _json_safe(extra_context)
        self._queue_dice_check(
            rule_name="resolve_check",
            args=normalized_args,
            version=None,
            reason=reason or action_text,
            result=result,
        )
        result["dice_check_note_queued"] = True
        input_payload["normalized_args"] = normalized_args
        self.repository.append_audit(
            self.session_id,
            {"type": "tool", "tool": "resolve_check", "input": input_payload, "result": result},
        )
        return result

    def _queue_dice_check(
        self,
        rule_name: str,
        args: Dict[str, Any],
        version: Optional[int],
        reason: str,
        result: Dict[str, Any],
    ) -> None:
        record = {
            "type": "dice_check",
            "rule_name": str(result.get("rule_name") or rule_name),
            "version": result.get("version", version),
            "reason": _dice_reason(reason=reason, args=args, rule_name=rule_name, message=self.message),
            "args": _json_safe(args),
            "ok": bool(result.get("ok")),
            "rolls": _json_safe(result.get("rolls") or []),
            "rule_result": _json_safe(result.get("result")),
            "error": str(result.get("error") or ""),
            "error_reason": str(result.get("reason") or ""),
            "actor": self.actor,
        }
        try:
            session = self.repository.load_session(self.session_id)
            pending = normalize_pending_outputs((session.scene or {}).get("_pending_outputs"))
            pending.append(record)
            session.scene["_pending_outputs"] = pending[-8:]
            self.repository.save_session(session)
        except Exception:
            return

    async def list_rules(self, detail_level: str = "summary", tag: str = "", limit: int = 16) -> Dict[str, Any]:
        """列出当前本地已经注册的规则，默认返回二级摘要以节省上下文。"""
        normalized_level = str(detail_level or "summary").strip().lower()
        if normalized_level not in {"summary", "detail"}:
            normalized_level = "summary"
        normalized_tag = str(tag or "").strip()
        normalized_limit = _clamp_int(limit, default=16, minimum=1, maximum=16)
        seen_key = (normalized_level, normalized_tag.lower(), normalized_limit)
        if seen_key in self._list_rules_seen:
            result: Dict[str, Any] = {
                "ok": True,
                "rules_reused": True,
                "detail_level": normalized_level,
                "tag": normalized_tag,
                "limit": normalized_limit,
                "hint": "本轮已返回过同参数规则列表；请使用上一条 list_rules 结果，不要重复查询。",
            }
            self.repository.append_audit(
                self.session_id,
                {
                    "type": "tool",
                    "tool": "list_rules",
                    "input": {"detail_level": detail_level, "tag": tag, "limit": limit},
                    "result": result,
                },
            )
            return result
        self._list_rules_seen.add(seen_key)
        raw_rules = self.rule_runtime.list_rules()
        refs = [RuleRef.from_dict(item) for item in raw_rules]
        if normalized_tag:
            wanted = normalized_tag.lower()
            refs = [rule for rule in refs if any(str(item).lower() == wanted for item in rule.tags)]
        large_unfiltered_detail = normalized_level == "detail" and not normalized_tag and len(refs) > 48
        detail_limit = min(normalized_limit, 4 if large_unfiltered_detail else 8)
        name_limit = 32 if len(refs) > 48 else 48
        rules_by_name = {rule.name: rule for rule in refs}
        result: Dict[str, Any] = {
            "ok": True,
            "rules": compact_rules(rules_by_name, detail_limit=detail_limit, name_limit=name_limit),
            "detail_level": normalized_level,
            "tag": normalized_tag,
            "limit": normalized_limit,
        }
        if large_unfiltered_detail:
            result["detail_restricted"] = True
            result["hint"] = "规则很多；无标签 detail 只返回最近少量详情。需要参数时请按 rules.level_1.by_tag 选择 tag 后再查 detail。"
        if normalized_level == "detail":
            result["details"] = [
                {
                    "name": rule.name,
                    "version": rule.version,
                    "description": rule.description,
                    "tags": rule.tags,
                    "input_schema": _compact_rule_schema(rule.input_schema),
                    "output_schema": _compact_rule_schema(rule.output_schema),
                    "updated_at": rule.updated_at,
                }
                for rule in sorted(refs, key=lambda item: (item.updated_at, item.name), reverse=True)[:detail_limit]
            ]
            if len(refs) > detail_limit:
                result["details_omitted"] = len(refs) - detail_limit
        self.repository.append_audit(
            self.session_id,
            {
                "type": "tool",
                "tool": "list_rules",
                "input": {"detail_level": detail_level, "tag": tag, "limit": limit},
                "result": result,
            },
        )
        return result


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _rule_validation_hint(reason: str) -> str:
    lowered = reason.lower()
    if "reserved helper name" in lowered:
        return "不要把 roll/randint 当作变量名或参数名；例如把 roll = roll('1d20') 改成 roll_total = roll('1d20')。"
    if "undefined name" in lowered:
        return "请检查变量名是否和 input_schema、calculate 参数或 kwargs.get(...) 中的名字一致。"
    return "请把规则限制为一个 calculate 函数，并只使用允许的纯计算语法。"


def _compact_rule_schema(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return _short_text(value, 80)
    if isinstance(value, dict):
        items = list(value.items())
        compacted = {
            str(key): _compact_rule_schema(item, depth=depth + 1)
            for key, item in items[:8]
        }
        omitted = len(items) - len(compacted)
        if omitted > 0:
            compacted["_omitted_keys"] = omitted
        return compacted
    if isinstance(value, list):
        compacted_list = [_compact_rule_schema(item, depth=depth + 1) for item in value[:8]]
        omitted = len(value) - len(compacted_list)
        if omitted > 0:
            compacted_list.append({"_omitted_items": omitted})
        return compacted_list
    if isinstance(value, str):
        return _short_text(value, 120)
    return value


def _normalize_execute_rule_args(rule_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(args or {})
    if "threshold" not in normalized and not any(key in normalized for key in ("dc", "target_dc", "difficulty", "target_number")):
        for key in ("target",):
            value = _number_or_none(normalized.get(key))
            if value is not None:
                normalized["threshold"] = int(value) if float(value).is_integer() else value
                break
    normalized = _drop_contextual_rule_args(normalized)
    if not _is_d20_like_rule(rule_name):
        return normalized
    normalized = _normalize_d20_rule_args(normalized)
    return {
        key: value
        for key, value in normalized.items()
        if str(key) not in D20_CONTEXTUAL_RULE_ARGS
    }


def _normalize_d20_rule_args(args: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(args or {})
    has_dc_like = any(
        key in normalized
        for key in ("dc", "target_dc", "difficulty_class", "target_number", "threshold", "difficulty")
    )
    if has_dc_like:
        dc_value, _warning = _resolve_check_dc(
            _first_existing_value(normalized, ("dc", "target_dc", "difficulty_class", "target_number", "threshold")),
            normalized.get("difficulty"),
        )
        normalized["dc"] = dc_value
    for key in ("target_dc", "difficulty_class", "target_number", "threshold", "difficulty"):
        normalized.pop(key, None)
    bonus_terms = (
        "skill_bonus",
        "ability_bonus",
        "ability_modifier",
        "proficiency_bonus",
        "situational_bonus",
        "situational",
        "item_bonus",
        "temporary_bonus",
        "temp_bonus",
        "long_term_bonus",
        "longterm_bonus",
    )
    explicit_bonus = _number_or_none(normalized.get("bonus"))
    if explicit_bonus is not None:
        modifier = explicit_bonus
    else:
        modifier = _number_or_none(normalized.get("modifier"))
        if modifier is None:
            modifier = 0
        for key in bonus_terms:
            value = _number_or_none(normalized.get(key))
            if value is not None:
                modifier += value
    penalty = _number_or_none(normalized.get("penalty"))
    if penalty is not None:
        modifier -= abs(penalty)
    normalized["bonus"] = int(modifier) if float(modifier).is_integer() else modifier
    normalized.pop("modifier", None)
    for key in bonus_terms + ("penalty",):
        normalized.pop(key, None)
    return normalized


CONTEXTUAL_EXECUTE_RULE_ARGS = {
    "approach",
    "attacker",
    "attacker_position",
    "context",
    "description",
    "environment",
    "enemy_awareness",
    "enemy_count",
    "hit_location",
    "hit_quality",
    "note",
    "notes",
    "reason",
    "situation",
    "target",
    "target_id",
    "target_aware",
    "target_name",
    "target_position",
    "target_size",
    "target_type",
    "terrain",
}


D20_CONTEXTUAL_RULE_ARGS = CONTEXTUAL_EXECUTE_RULE_ARGS | {
    "ability",
    "advantage",
    "character_name",
    "check_type",
    "description",
    "disadvantage",
    "modifier_note",
    "proficiency",
    "proficient",
    "skill",
    "stakes",
}


def _drop_contextual_rule_args(args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in dict(args or {}).items()
        if str(key) not in CONTEXTUAL_EXECUTE_RULE_ARGS
    }


def _augment_check_result(rule_name: str, args: Dict[str, Any], result: Dict[str, Any]) -> None:
    if not result.get("ok"):
        return
    payload = result.get("result")
    if not isinstance(payload, dict):
        return
    total = _first_numeric(payload, ("total", "result", "check_total"))
    if total is None:
        total = _roll_total_from_records(result.get("rolls"))
    dc = _first_numeric(args, ("dc", "target_dc", "difficulty", "target_number", "threshold", "ac", "armor_class"))
    if dc is None:
        dc = _first_numeric(payload, ("dc", "target_dc", "difficulty", "target_number", "threshold", "ac", "armor_class"))
    if total is None:
        return
    roll = _first_numeric(payload, ("roll", "d20", "die", "die_roll"))
    if roll is None:
        roll = _roll_value_from_records(result.get("rolls"))
    modifier = _first_numeric(payload, ("modifier", "bonus", "total_bonus"))
    if modifier is None and roll is not None:
        modifier = total - roll
    payload.setdefault("total", int(total) if float(total).is_integer() else total)
    if roll is not None:
        payload.setdefault("roll", int(roll) if float(roll).is_integer() else roll)
    if modifier is not None:
        payload["modifier"] = int(modifier) if float(modifier).is_integer() else modifier
    if dc is None:
        return
    payload["dc"] = int(dc) if float(dc).is_integer() else dc
    success = total >= dc
    if "success" not in payload and "is_success" not in payload:
        payload["success"] = success
    payload["margin"] = int(total - dc) if float(total - dc).is_integer() else total - dc
    result["check"] = {
        "rule_name": _canonical_rule_name(rule_name),
        "total": payload["total"],
        "dc": payload["dc"],
        "success": bool(payload.get("success", payload.get("is_success", success))),
        "margin": payload["margin"],
    }


def _is_d20_like_rule(rule_name: str) -> bool:
    name = _canonical_rule_name(rule_name)
    return name in {"d20_check", "roll_d20_safe", "roll_d20_test"} or ("d20" in name and "check" in name)


def _canonical_rule_name(rule_name: str) -> str:
    name = str(rule_name or "").strip().lower()
    name = name.split("@", 1)[0]
    return re.sub(r"_v\d+$", "", name)


def _first_numeric(mapping: Dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _number_or_none(mapping.get(key))
        if value is not None:
            return value
    return None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _first_existing_value(mapping: Dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, ""):
            return mapping.get(key)
    return None


DIFFICULTY_DC_MAP = {
    "trivial": 5,
    "very_easy": 8,
    "very easy": 8,
    "easy": 10,
    "medium": 15,
    "moderate": 15,
    "normal": 15,
    "hard": 18,
    "very_hard": 22,
    "very hard": 22,
    "extreme": 25,
    "nearly_impossible": 30,
    "nearly impossible": 30,
    "simple": 10,
    "简单": 10,
    "容易": 10,
    "普通": 15,
    "一般": 15,
    "中等": 15,
    "困难": 18,
    "很难": 22,
    "极难": 25,
    "近乎不可能": 30,
    "几乎不可能": 30,
}


def _resolve_check_dc(dc: Any, difficulty: Any = "") -> tuple[int, str]:
    dc_number = _number_or_none(dc)
    if dc_number is not None:
        return _clamp_int(dc_number, default=10, minimum=1, maximum=40), ""
    difficulty_number = _number_or_none(difficulty)
    if difficulty_number is not None:
        return _clamp_int(difficulty_number, default=10, minimum=1, maximum=40), ""
    key = str(difficulty or "").strip().lower().replace("-", "_")
    if key in DIFFICULTY_DC_MAP:
        value = DIFFICULTY_DC_MAP[key]
        return value, f"mapped difficulty {difficulty!r} to DC {value}"
    return 10, "defaulted missing difficulty to DC 10"


def _resolve_check_bonus(bonus: Any, extra_context: Dict[str, Any]) -> int:
    explicit_bonus = _number_or_none(bonus)
    if explicit_bonus is not None:
        return int(round(explicit_bonus))
    total = 0.0
    found = False
    for key in (
        "modifier",
        "skill_bonus",
        "ability_bonus",
        "ability_modifier",
        "proficiency_bonus",
        "situational_bonus",
        "item_bonus",
        "temporary_bonus",
        "temp_bonus",
    ):
        value = _number_or_none(extra_context.get(key))
        if value is not None:
            total += value
            found = True
    penalty = _number_or_none(extra_context.get("penalty"))
    if penalty is not None:
        total -= abs(penalty)
        found = True
    return int(round(total)) if found else 0


def _normalize_advantage(advantage: Any, extra_context: Dict[str, Any]) -> str:
    if isinstance(advantage, bool):
        return "advantage" if advantage else "normal"
    if isinstance(extra_context.get("disadvantage"), bool) and extra_context.get("disadvantage"):
        return "disadvantage"
    text = str(advantage or "").strip().lower()
    if text in {"adv", "advantage", "true", "yes", "y", "优势", "有优势"}:
        return "advantage"
    if text in {"dis", "disadvantage", "disadv", "劣势", "有劣势"}:
        return "disadvantage"
    return "normal"


def _roll_d20_for_advantage(advantage: str) -> list[int]:
    if advantage in {"advantage", "disadvantage"}:
        return [random.randint(1, 20), random.randint(1, 20)]
    return [random.randint(1, 20)]


def _check_outcome(*, kept_roll: int, total: int, dc: int) -> str:
    if kept_roll == 20 and total >= dc:
        return "critical_success"
    if kept_roll == 1 and total < dc:
        return "failure"
    if total >= dc:
        return "success"
    if total >= dc - 5:
        return "partial_success"
    return "failure"


def _narrative_guidance_for_outcome(outcome: str) -> str:
    if outcome == "critical_success":
        return "The action succeeds cleanly; add an extra benefit only if it fits the scene."
    if outcome == "success":
        return "The action succeeds; write only consequences supported by the check."
    if outcome == "partial_success":
        return "The action may progress, but include a cost, trace, complication, or limited effect."
    return "The action fails or creates a serious complication; do not write a clean success."


def _new_check_id(*, actor_id: str, actor_name: str) -> str:
    actor = re.sub(r"[^a-zA-Z0-9_]+", "_", str(actor_id or actor_name or "actor")).strip("_") or "actor"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return f"chk_{stamp}_{actor}"[:80]


def _roll_value_from_records(records: Any) -> float | None:
    if not isinstance(records, list) or not records:
        return None
    first = records[0]
    if not isinstance(first, dict):
        return None
    rolls = first.get("rolls")
    if isinstance(rolls, list) and len(rolls) == 1:
        return _number_or_none(rolls[0])
    return None


def _roll_total_from_records(records: Any) -> float | None:
    if not isinstance(records, list) or not records:
        return None
    first = records[0]
    if not isinstance(first, dict):
        return None
    return _number_or_none(first.get("total"))


def _dice_reason(reason: str, args: Dict[str, Any], rule_name: str, message: str) -> str:
    for value in (
        reason,
        args.get("reason"),
        args.get("check_reason"),
        args.get("purpose"),
        args.get("intent"),
        args.get("action"),
        args.get("description"),
    ):
        text = str(value or "").strip()
        if text:
            return _short_text(text, 120)
    if message:
        return _short_text(f"玩家行动需要随机裁定：{message}", 120)
    return f"执行规则 {rule_name} 的随机检定"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _short_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"

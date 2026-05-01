from __future__ import annotations

import hashlib
import inspect
import json
import multiprocessing as mp
import re
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..core.models import RuleRef, utc_now_iso
from .dice import DiceRoller
from .validator import RuleValidationError, RuleValidator


SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "pow": pow,
    "range": range,
    "round": round,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
}


class PythonRuleRuntime:
    def __init__(self, rules_dir: Path, timeout_seconds: float = 2.0):
        self.rules_dir = rules_dir
        self.rules_dir.mkdir(parents=True, exist_ok=True)
        self.validator = RuleValidator()
        self.timeout_seconds = timeout_seconds

    def register_rule(
        self,
        rule_name: str,
        description: str,
        code_string: str,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        safe_name = self._safe_rule_name(rule_name)
        if not safe_name:
            return {"ok": False, "error": "invalid_rule_name"}
        try:
            self.validator.validate(code_string)
        except RuleValidationError as exc:
            return {"ok": False, "error": "validation_failed", "reason": str(exc)}

        version = self._next_version(safe_name)
        code_hash = hashlib.sha256(code_string.encode("utf-8")).hexdigest()
        rule_dir = self.rules_dir / safe_name
        rule_dir.mkdir(parents=True, exist_ok=True)
        code_path = rule_dir / f"v{version}.py"
        meta_path = rule_dir / f"v{version}.json"
        code_path.write_text(code_string, encoding="utf-8")
        metadata = RuleRef(
            name=safe_name,
            version=version,
            description=description,
            input_schema=input_schema or {},
            output_schema=output_schema or {},
            tags=tags or [],
            code_hash=code_hash,
            updated_at=utc_now_iso(),
        )
        meta_path.write_text(json.dumps(asdict(metadata), ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "ok": True,
            "rule_name": safe_name,
            "version": version,
            "hash": code_hash,
            "warnings": [
                "python_subset runtime is an MVP guardrail, not a strong security boundary"
            ],
        }

    def execute_rule(
        self,
        rule_name: str,
        args: dict[str, Any],
        version: int | None = None,
    ) -> dict[str, Any]:
        safe_name = self._safe_rule_name(rule_name)
        resolved_name, selected = self._select_rule(safe_name, version)
        if selected is None:
            return {"ok": False, "error": "rule_not_found", "rule_name": safe_name}
        selected_version, code_path = selected
        code = code_path.read_text(encoding="utf-8")
        original_args = dict(args or {})
        execution_args = _coerce_rule_args_by_schema(
            original_args,
            self._load_input_schema(resolved_name, selected_version),
        )
        try:
            payload = self._execute_in_process(code, execution_args)
        except (OSError, PermissionError):
            payload = self._execute_in_thread(code, execution_args)
            payload.setdefault("warnings", []).append(
                "process isolation unavailable; executed with restricted globals in current process"
            )
        if payload.get("error") == "rule_process_failed":
            payload = self._execute_in_thread(code, execution_args)
            payload.setdefault("warnings", []).append(
                "process isolation failed; executed with restricted globals in current process"
            )
        if _should_retry_with_numeric_args(payload):
            coerced_args = _coerce_rule_args_for_retry(execution_args)
            if coerced_args != execution_args:
                retry_payload = self._execute_in_thread(code, coerced_args)
                if retry_payload.get("ok"):
                    payload = retry_payload
                    payload["coerced_args"] = coerced_args
                    payload.setdefault("warnings", []).append(
                        "coerced non-numeric rule arguments for retry"
                    )
        if execution_args != original_args:
            payload.setdefault("coerced_args", execution_args)
            payload.setdefault("warnings", []).append(
                "coerced numeric rule arguments from input_schema"
            )
        payload["rule_name"] = resolved_name
        payload["version"] = selected_version
        if resolved_name != safe_name:
            payload["requested_rule_name"] = safe_name
        return payload

    def _execute_in_process(self, code: str, args: dict[str, Any]) -> dict[str, Any]:
        queue: mp.Queue = mp.Queue()
        process = mp.Process(target=_execute_rule_worker, args=(code, args, queue))
        process.start()
        process.join(self.timeout_seconds)
        if process.is_alive():
            process.terminate()
            process.join(1)
            return {"ok": False, "error": "rule_timeout"}
        if queue.empty():
            return {"ok": False, "error": "rule_process_failed"}
        return queue.get()

    def _execute_in_thread(self, code: str, args: dict[str, Any]) -> dict[str, Any]:
        box: dict[str, Any] = {}
        thread = threading.Thread(
            target=lambda: box.update(_execute_rule_direct(code, args)),
            daemon=True,
        )
        thread.start()
        thread.join(self.timeout_seconds)
        if thread.is_alive():
            return {"ok": False, "error": "rule_timeout"}
        return box or {"ok": False, "error": "rule_thread_failed"}

    def list_rules(self) -> list[dict[str, Any]]:
        rules: list[dict[str, Any]] = []
        for rule_dir in sorted(self.rules_dir.iterdir()):
            if not rule_dir.is_dir():
                continue
            selected = self._select_version(rule_dir.name, None)
            if not selected:
                continue
            version, _ = selected
            meta_path = rule_dir / f"v{version}.json"
            if meta_path.exists():
                rules.append(json.loads(meta_path.read_text(encoding="utf-8")))
        return rules

    def load_rule_ref(self, rule_name: str, version: int | None = None) -> RuleRef | None:
        safe_name = self._safe_rule_name(rule_name)
        resolved_name, selected = self._select_rule(safe_name, version)
        if not selected:
            return None
        selected_version, _ = selected
        meta_path = self.rules_dir / resolved_name / f"v{selected_version}.json"
        if not meta_path.exists():
            return None
        return RuleRef.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))

    def _load_input_schema(self, safe_name: str, version: int) -> dict[str, Any]:
        meta_path = self.rules_dir / safe_name / f"v{version}.json"
        if not meta_path.exists():
            return {}
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        input_schema = metadata.get("input_schema")
        return input_schema if isinstance(input_schema, dict) else {}

    def _next_version(self, safe_name: str) -> int:
        rule_dir = self.rules_dir / safe_name
        versions = self._versions(rule_dir)
        return max(versions, default=0) + 1

    def _select_version(self, safe_name: str, version: int | None) -> tuple[int, Path] | None:
        if not safe_name:
            return None
        rule_dir = self.rules_dir / safe_name
        if version is None:
            versions = self._versions(rule_dir)
            if not versions:
                return None
            version = max(versions)
        code_path = rule_dir / f"v{version}.py"
        if not code_path.exists():
            return None
        return version, code_path

    def _select_rule(self, safe_name: str, version: int | None) -> tuple[str, tuple[int, Path] | None]:
        selected = self._select_version(safe_name, version)
        if selected:
            return safe_name, selected
        for alias in self._rule_name_aliases(safe_name, version):
            selected = self._select_version(alias, None)
            if selected:
                return alias, selected
        return safe_name, None

    def _rule_name_aliases(self, safe_name: str, version: int | None) -> list[str]:
        aliases: list[str] = []
        if version:
            aliases.append(f"{safe_name}_v{version}")
        prefix = f"{safe_name}_v"
        if self.rules_dir.exists():
            for rule_dir in sorted(self.rules_dir.iterdir()):
                if rule_dir.is_dir() and rule_dir.name.startswith(prefix):
                    suffix = rule_dir.name[len(prefix):]
                    if suffix.isdigit():
                        aliases.append(rule_dir.name)
        return list(dict.fromkeys(aliases))

    @staticmethod
    def _versions(rule_dir: Path) -> list[int]:
        if not rule_dir.exists():
            return []
        versions: list[int] = []
        for path in rule_dir.glob("v*.py"):
            match = re.fullmatch(r"v(\d+)\.py", path.name)
            if match:
                versions.append(int(match.group(1)))
        return versions

    @staticmethod
    def _safe_rule_name(rule_name: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", rule_name.strip())
        return safe.strip("._-")


def _execute_rule_worker(code: str, args: dict[str, Any], queue: mp.Queue) -> None:
    queue.put(_execute_rule_direct(code, args))


def _execute_rule_direct(code: str, args: dict[str, Any]) -> dict[str, Any]:
    roller = DiceRoller()
    globals_dict: dict[str, Any] = {
        "__builtins__": SAFE_BUILTINS,
        "roll": roller.roll,
        "randint": roller.randint,
    }
    locals_dict: dict[str, Any] = {}
    try:
        exec(compile(code, "<trpg_rule>", "exec"), globals_dict, locals_dict)
        calculate = locals_dict.get("calculate")
        if not callable(calculate):
            return {"ok": False, "error": "missing_calculate"}
        call_args = _filter_calculate_args(calculate, args)
        if call_args.get("missing"):
            missing = ", ".join(call_args["missing"])
            return {
                "ok": False,
                "error": "invalid_rule_arguments",
                "reason": f"missing required rule arguments: {missing}",
                "missing_arguments": call_args["missing"],
                "rolls": roller.dump(),
            }
        result = calculate(**call_args["args"])
        payload: dict[str, Any] = {"ok": True, "result": result, "rolls": roller.dump()}
        if call_args["ignored"]:
            payload["ignored_args"] = call_args["ignored"]
        return payload
    except Exception as exc:
        return {"ok": False, "error": "rule_exception", "reason": str(exc), "rolls": roller.dump()}


def _should_retry_with_numeric_args(payload: dict[str, Any]) -> bool:
    reason = str(payload.get("reason") or "")
    return payload.get("error") == "rule_exception" and (
        ("unsupported operand type" in reason and "'str'" in reason)
        or ("not supported between instances" in reason and "'str'" in reason)
        or "invalid literal for int()" in reason
        or "could not convert string to float" in reason
    )


def _coerce_rule_args_by_schema(args: dict[str, Any], input_schema: dict[str, Any]) -> dict[str, Any]:
    properties = input_schema.get("properties") if isinstance(input_schema, dict) else {}
    if not isinstance(properties, dict):
        return dict(args or {})
    coerced = dict(args or {})
    for key, property_schema in properties.items():
        if key not in coerced or not _schema_declares_numeric(property_schema):
            continue
        value = _coerce_rule_arg_value_for_schema(coerced[key], str(key), property_schema)
        if _schema_declares_integer(property_schema) and isinstance(value, float):
            value = int(round(value))
        coerced[key] = value
    return coerced


def _schema_declares_numeric(property_schema: Any) -> bool:
    types = _schema_type_names(property_schema)
    return bool({"integer", "number"} & types)


def _schema_declares_integer(property_schema: Any) -> bool:
    return "integer" in _schema_type_names(property_schema)


def _schema_type_names(property_schema: Any) -> set[str]:
    if not isinstance(property_schema, dict):
        return set()
    type_value = property_schema.get("type")
    if isinstance(type_value, str):
        return {type_value}
    if isinstance(type_value, list):
        return {str(item) for item in type_value}
    return set()


def _coerce_rule_arg_value_for_schema(value: Any, key: str, property_schema: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not text:
        return _schema_neutral_numeric_value(key, property_schema)
    try:
        number = float(text)
        if _schema_looks_like_ten_scale(key, property_schema):
            number = max(1, min(10, number))
        return int(number) if number.is_integer() else number
    except ValueError:
        pass
    if _schema_looks_like_ten_scale(key, property_schema):
        return _coerce_ten_scale_value(text)
    value = _coerce_rule_arg_value(value)
    if value == 0:
        return _schema_neutral_numeric_value(key, property_schema)
    return value


def _schema_looks_like_ten_scale(key: str, property_schema: Any) -> bool:
    lowered = _schema_hint_text(key, property_schema)
    return any(
        term in lowered
        for term in (
            "1-10",
            "1~10",
            "1 到 10",
            "1至10",
            "1到10",
            "1..10",
            "1/10",
            "十分",
            "十级",
            "scale 1",
            "1 to 10",
        )
    )


def _schema_neutral_numeric_value(key: str, property_schema: Any) -> int:
    lowered = _schema_hint_text(key, property_schema)
    if any(term in lowered for term in ("bonus", "modifier", "penalty", "加成", "修正", "惩罚")):
        return 0
    if _schema_looks_like_ten_scale(key, property_schema):
        return 5
    return 0


def _schema_hint_text(key: str, property_schema: Any) -> str:
    parts = [key]
    if isinstance(property_schema, dict):
        for field in ("title", "description"):
            value = property_schema.get(field)
            if value:
                parts.append(str(value))
    return " ".join(parts).lower()


def _coerce_ten_scale_value(value: str) -> int:
    lowered = value.lower()
    high_terms = (
        "极高",
        "很高",
        "强",
        "大量",
        "丰富",
        "高",
        "近距",
        "接触",
        "上风",
        "下风",
        "顺风",
        "迎风",
        "有风",
        "大风",
        "强风",
        "contact",
        "high",
        "large",
        "rich",
        "abundant",
        "close",
        "windy",
        "upwind",
        "downwind",
        "tailwind",
        "headwind",
    )
    low_terms = (
        "极低",
        "很低",
        "低",
        "很少",
        "稀少",
        "小",
        "弱",
        "远",
        "无风",
        "避风",
        "small",
        "low",
        "weak",
        "far",
        "scarce",
        "calm",
    )
    mid_terms = ("中等", "适中", "中", "普通", "一般", "medium", "moderate", "normal")
    if any(term in lowered for term in high_terms):
        return 8
    if any(term in lowered for term in low_terms):
        return 2
    if any(term in lowered for term in mid_terms):
        return 5
    return 5


def _coerce_rule_args_for_retry(args: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _coerce_rule_arg_value(value) for key, value in dict(args or {}).items()}


def _coerce_rule_arg_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _coerce_rule_arg_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_coerce_rule_arg_value(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not text:
        return 0
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        pass
    lowered = text.lower()
    score = 0
    if any(term in lowered for term in ("上风", "下风", "顺风", "迎风", "有风", "大风", "强风", "windy", "upwind", "downwind", "tailwind", "headwind")):
        score += 3
    if any(term in lowered for term in ("微风", "弱风", "无风", "避风", "breeze", "calm")):
        score += 1
    if any(term in lowered for term in ("极高", "很高", "强", "大", "高", "近距", "接触", "contact", "high", "large", "close")):
        score += 3
    if any(term in lowered for term in ("中等", "中", "普通", "适应", "medium", "moderate", "normal")):
        score += 2
    if any(term in lowered for term in ("低", "小", "弱", "远", "small", "low", "weak", "far")):
        score += 1
    if any(term in lowered for term in ("谨慎", "小心", "cautious")):
        score += 1
    if any(term in lowered for term in ("直接", "鲁莽", "无观察", "reckless", "direct")):
        score -= 1
    return max(-5, min(5, score))


def _filter_calculate_args(calculate: Any, args: dict[str, Any]) -> dict[str, Any]:
    try:
        signature = inspect.signature(calculate)
    except (TypeError, ValueError):
        return {"args": dict(args), "ignored": []}
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return {"args": dict(args), "ignored": [], "missing": []}
    if _accepts_single_kwargs_mapping(signature):
        return {"args": {"kwargs": dict(args)}, "ignored": [], "missing": []}
    accepted = {
        name
        for name, param in signature.parameters.items()
        if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    if not accepted:
        return {"args": {}, "ignored": sorted(str(key) for key in args), "missing": []}
    filtered = {key: value for key, value in args.items() if key in accepted}
    ignored = sorted(str(key) for key in args if key not in accepted)
    missing = sorted(
        name
        for name, param in signature.parameters.items()
        if name in accepted
        and name not in filtered
        and param.default is inspect.Parameter.empty
    )
    return {"args": filtered, "ignored": ignored, "missing": missing}


def _accepts_single_kwargs_mapping(signature: inspect.Signature) -> bool:
    parameters = list(signature.parameters.values())
    return (
        len(parameters) == 1
        and parameters[0].name == "kwargs"
        and parameters[0].kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    )

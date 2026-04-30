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
        selected = self._select_version(safe_name, version)
        if selected is None:
            return {"ok": False, "error": "rule_not_found", "rule_name": safe_name}
        selected_version, code_path = selected
        code = code_path.read_text(encoding="utf-8")
        try:
            payload = self._execute_in_process(code, args)
        except (OSError, PermissionError):
            payload = self._execute_in_thread(code, args)
            payload.setdefault("warnings", []).append(
                "process isolation unavailable; executed with restricted globals in current process"
            )
        if payload.get("error") == "rule_process_failed":
            payload = self._execute_in_thread(code, args)
            payload.setdefault("warnings", []).append(
                "process isolation failed; executed with restricted globals in current process"
            )
        payload["rule_name"] = safe_name
        payload["version"] = selected_version
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
        selected = self._select_version(safe_name, version)
        if not selected:
            return None
        selected_version, _ = selected
        meta_path = self.rules_dir / safe_name / f"v{selected_version}.json"
        if not meta_path.exists():
            return None
        return RuleRef.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))

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
        result = calculate(**call_args["args"])
        payload: dict[str, Any] = {"ok": True, "result": result, "rolls": roller.dump()}
        if call_args["ignored"]:
            payload["ignored_args"] = call_args["ignored"]
        return payload
    except Exception as exc:
        return {"ok": False, "error": "rule_exception", "reason": str(exc), "rolls": roller.dump()}


def _filter_calculate_args(calculate: Any, args: dict[str, Any]) -> dict[str, Any]:
    try:
        signature = inspect.signature(calculate)
    except (TypeError, ValueError):
        return {"args": dict(args), "ignored": []}
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return {"args": dict(args), "ignored": []}
    accepted = {
        name
        for name, param in signature.parameters.items()
        if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    if not accepted:
        return {"args": {}, "ignored": sorted(str(key) for key in args)}
    filtered = {key: value for key, value in args.items() if key in accepted}
    ignored = sorted(str(key) for key in args if key not in accepted)
    return {"args": filtered, "ignored": ignored}

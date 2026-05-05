from __future__ import annotations

from typing import Any


DM_PROMPT_BLOCKED_KEYS = {
    "api_key",
    "audit",
    "authorization",
    "debug",
    "diagnostic",
    "diagnostics",
    "file_path",
    "grid",
    "headers",
    "html",
    "image_bytes",
    "local_path",
    "metadata_path",
    "password",
    "path",
    "prompt",
    "provider",
    "raw",
    "raw_audit",
    "raw_content",
    "raw_excerpt",
    "raw_output",
    "raw_player_input",
    "raw_ra_output",
    "raw_svg",
    "raw_text",
    "rule_packages",
    "rule_sets",
    "source_url",
    "svg",
    "system_prompt",
    "token_usage",
    "tool_trace",
    "tool_traces",
    "url",
    "web_grounding",
}

DM_PROMPT_BLOCKED_KEY_TOKENS = (
    "api_key",
    "authorization",
    "credential",
    "debug",
    "diagnostic",
    "metadata_path",
    "password",
    "provider_payload",
    "raw_",
    "secret",
    "system_prompt",
    "token_usage",
)

DM_PROMPT_TEXT_LIMIT = 700
DM_PROMPT_LIST_LIMIT = 12
DM_PROMPT_MAPPING_LIMIT = 24


def project_tool_results_for_dm_prompt(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for item in tool_results or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool") or item.get("name") or "")
        projected_item: dict[str, Any] = {"tool": name}
        args = project_dm_prompt_value(item.get("args", {}), depth=3, text_limit=240)
        if args not in ({}, [], "", None):
            projected_item["args"] = args
        projected_item["result"] = project_dm_prompt_value(
            item.get("result", {}),
            depth=5,
            text_limit=DM_PROMPT_TEXT_LIMIT,
        )
        projected.append(projected_item)
    return projected


def project_ra_summary_for_dm_prompt(ra_summary: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ra_summary, dict):
        return {}
    projected: dict[str, Any] = {}
    for key in (
        "cycle_id",
        "summary",
        "rules_triggered",
        "dm_narrative_aligned",
        "discrepancies",
        "created_at",
    ):
        if key in ra_summary:
            projected[key] = project_dm_prompt_value(ra_summary.get(key), depth=3, text_limit=500)
    validation = _patch_validation_counts(ra_summary.get("patch_validation"))
    if validation:
        projected["patch_validation"] = validation
    return projected


def project_dm_prompt_value(
    value: Any,
    *,
    depth: int = 4,
    text_limit: int = DM_PROMPT_TEXT_LIMIT,
) -> Any:
    if _hidden_or_diagnostic_record(value):
        return {}
    if isinstance(value, dict):
        if depth <= 0:
            return {"keys": _safe_mapping_keys(value)}
        projected: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= DM_PROMPT_MAPPING_LIMIT:
                projected["_truncated_items"] = max(0, len(value) - DM_PROMPT_MAPPING_LIMIT)
                break
            key_text = str(key)
            if _blocked_dm_prompt_key(key_text):
                continue
            if _hidden_or_diagnostic_record(item):
                continue
            projected_value = project_dm_prompt_value(item, depth=depth - 1, text_limit=text_limit)
            if projected_value not in ({}, [], "", None):
                projected[key_text] = projected_value
        return projected
    if isinstance(value, list):
        projected_items = [
            project_dm_prompt_value(item, depth=depth - 1, text_limit=text_limit)
            for item in value[:DM_PROMPT_LIST_LIMIT]
            if not _hidden_or_diagnostic_record(item)
        ]
        return [item for item in projected_items if item not in ({}, [], "", None)]
    if isinstance(value, str):
        return _compact_text(value, text_limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _compact_text(value, 200)


def _patch_validation_counts(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    accepted = [item for item in value.get("accepted") or [] if isinstance(item, dict)]
    rejected = [item for item in value.get("rejected") or [] if isinstance(item, dict)]
    result: dict[str, Any] = {
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
    }
    if accepted:
        result["accepted_categories"] = sorted(
            {str(item.get("category") or "") for item in accepted if item.get("category")}
        )
    if rejected:
        result["rejected_categories"] = sorted(
            {str(item.get("category") or "") for item in rejected if item.get("category")}
        )
        result["rejected_reasons"] = sorted(
            {str(item.get("reason") or "") for item in rejected if item.get("reason")}
        )
    return {key: item for key, item in result.items() if item not in ([], "", None)}


def _hidden_or_diagnostic_record(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    visibility = str(value.get("visibility") or "").strip().lower()
    return visibility in {"hidden", "diagnostic"}


def _blocked_dm_prompt_key(key: str) -> bool:
    key_lower = key.strip().lower()
    if key_lower in DM_PROMPT_BLOCKED_KEYS:
        return True
    return any(token in key_lower for token in DM_PROMPT_BLOCKED_KEY_TOKENS)


def _safe_mapping_keys(value: dict[str, Any]) -> list[str]:
    return [
        str(key)
        for key in value.keys()
        if not _blocked_dm_prompt_key(str(key))
    ][:DM_PROMPT_MAPPING_LIMIT]


def _compact_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."

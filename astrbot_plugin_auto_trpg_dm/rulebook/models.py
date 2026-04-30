from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceRef:
    book: str = ""
    path: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SourceRef":
        data = data or {}
        return cls(book=str(data.get("book") or ""), path=str(data.get("path") or ""))

    def to_dict(self) -> dict[str, str]:
        return {"book": self.book, "path": self.path}


@dataclass
class RuleCard:
    id: str
    title: str
    category: str
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    procedure: list[str] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    related_rule_ids: list[str] = field(default_factory=list)
    source_refs: list[SourceRef] = field(default_factory=list)
    review_status: str = "generated"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuleCard":
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            category=str(data.get("category") or "core_mechanics"),
            aliases=_strings(data.get("aliases")),
            tags=_strings(data.get("tags")),
            summary=str(data.get("summary") or ""),
            procedure=_strings(data.get("procedure")),
            exceptions=_strings(data.get("exceptions")),
            related_rule_ids=_strings(data.get("related_rule_ids")),
            source_refs=[SourceRef.from_dict(item) for item in _dicts(data.get("source_refs"))],
            review_status=str(data.get("review_status") or "generated"),
        )

    def to_public_dict(self, score: float | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "summary": self.summary,
            "procedure": self.procedure,
            "exceptions": self.exceptions,
            "related_rule_ids": self.related_rule_ids,
            "source_refs": [item.to_dict() for item in self.source_refs],
            "review_status": self.review_status,
        }
        if score is not None:
            payload["score"] = round(float(score), 3)
        return payload


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


PHASE1_DIRS = (
    Path("玩家手册2024") / "进行游戏",
    Path("玩家手册2024") / "术语汇编",
)

DM_GUIDANCE_FILES = (
    Path("城主指南2024") / "1.基础" / "DM是做什么的.htm",
    Path("城主指南2024") / "1.基础" / "每个DM都是独一无二的.htm",
    Path("城主指南2024") / "1.基础" / "确保所有人玩得开心" / "相互尊重.htm",
    Path("城主指南2024") / "1.基础" / "确保所有人玩得开心" / "尊重玩家.htm",
    Path("城主指南2024") / "2.运作游戏" / "了解你的玩家.htm",
    Path("城主指南2024") / "2.运作游戏" / "叙事.htm",
    Path("城主指南2024") / "2.运作游戏" / "决定掷骰结果" / "即兴答复.htm",
    Path("城主指南2024") / "2.运作游戏" / "决定掷骰结果" / "后果.htm",
    Path("城主指南2024") / "2.运作游戏" / "运作战斗" / "战斗中的叙述.htm",
)

RULEBOOK_SCOPE = (*PHASE1_DIRS, *DM_GUIDANCE_FILES)


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.current_heading = ""
        self.headings: list[str] = []
        self.paragraphs: list[str] = []
        self.table_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self.skip_depth += 1
            return
        if tag in {"h1", "h2", "h3"}:
            self.current_heading = tag
        if tag == "table":
            self.table_count += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
        if tag in {"h1", "h2", "h3"}:
            self.current_heading = ""

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = _clean_text(data)
        if not text:
            return
        if self.current_heading:
            if text not in self.headings:
                self.headings.append(text)
            return
        if len(text) >= 2:
            self.paragraphs.append(text)


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    chm_path = Path(args.chm).resolve()
    out_dir = Path(args.out).resolve()
    seed_dir = repo_root / "astrbot_plugin_auto_trpg_dm" / "rulebook" / "seed" / "dnd2024_core"

    seed_cards = _read_jsonl(seed_dir / "rule_cards.jsonl")
    seed_aliases = _read_json(seed_dir / "aliases.json")
    seed_source_map = _read_json(seed_dir / "source_map.json")
    if not seed_cards:
        raise SystemExit(f"seed cards not found: {seed_dir / 'rule_cards.jsonl'}")
    if not chm_path.exists():
        raise SystemExit(f"CHM not found: {chm_path}")

    work_dir = Path(args.work_dir).resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix="dnd2024_core_chm_"))
    work_dir.mkdir(parents=True, exist_ok=True)
    decompile_chm(chm_path, work_dir)
    page_records = collect_page_records(
        work_dir,
        include_raw_text=args.include_raw_text,
        raw_text_chars=args.raw_text_chars,
        extra_source_paths=_source_paths(seed_cards),
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    source_map = _refresh_source_map(seed_source_map, page_records)
    cards = _refresh_cards(seed_cards, source_map)
    warnings = _review_warnings(cards, page_records, source_map)

    _write_json(
        out_dir / "manifest.json",
        {
            "rulebook_id": "dnd2024_core",
            "rulebook_version": "local-build-2026-04-30",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "source_chm": str(chm_path),
            "source_chm_size": chm_path.stat().st_size,
            "scope": [str(item).replace("\\", "/") for item in RULEBOOK_SCOPE],
            "card_count": len(cards),
            "page_count": len(page_records),
            "include_raw_text": bool(args.include_raw_text),
            "copyright_boundary": "Runtime cards contain concise summaries and source paths only; raw text extraction is disabled unless explicitly requested.",
        },
    )
    _write_jsonl(out_dir / "rule_cards.jsonl", cards)
    _write_json(out_dir / "aliases.json", seed_aliases)
    _write_json(out_dir / "source_map.json", source_map)
    _write_json(out_dir / "search_index.json", build_search_index(cards))
    _write_json(out_dir / "tables.json", {})
    _write_jsonl(out_dir / "raw_page_records.jsonl", page_records)
    (out_dir / "review_warnings.md").write_text("\n".join(warnings) + "\n", encoding="utf-8")

    if not args.keep_decompiled and not args.work_dir:
        shutil.rmtree(work_dir, ignore_errors=True)
    print(json.dumps({"ok": True, "out": str(out_dir), "cards": len(cards), "pages": len(page_records)}, ensure_ascii=False))
    return 0


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Build optional DND 2024 core rulebook cards for Auto TRPG DM.")
    parser.add_argument("--chm", default=str(repo_root / "dnd2024_core_rules" / "DND.v2026.02.12.chm"))
    parser.add_argument(
        "--out",
        default=str(repo_root / "astrbot_plugin_auto_trpg_dm" / "data" / "rulebooks" / "dnd2024_core"),
    )
    parser.add_argument("--work-dir", default="")
    parser.add_argument("--keep-decompiled", action="store_true")
    parser.add_argument("--include-raw-text", action="store_true")
    parser.add_argument("--raw-text-chars", type=int, default=0)
    return parser.parse_args()


def decompile_chm(chm_path: Path, out_dir: Path) -> None:
    if any(out_dir.rglob("*.htm")) or any(out_dir.rglob("*.html")):
        return
    completed = subprocess.run(
        ["hh.exe", "-decompile", str(out_dir), str(chm_path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"hh.exe decompile failed: {completed.stderr or completed.stdout}")


def collect_page_records(
    root: Path,
    *,
    include_raw_text: bool,
    raw_text_chars: int,
    extra_source_paths: set[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    allowed_files: set[Path] = set()
    for directory in PHASE1_DIRS:
        base = root / directory
        if base.exists():
            allowed_files.update(path for path in base.rglob("*") if path.suffix.lower() in {".htm", ".html"})
    for source_file in DM_GUIDANCE_FILES:
        candidate = root / source_file
        if candidate.exists() and candidate.suffix.lower() in {".htm", ".html"}:
            allowed_files.add(candidate)
    for source_path in extra_source_paths:
        candidate = root / Path(source_path)
        if candidate.exists() and candidate.suffix.lower() in {".htm", ".html"}:
            allowed_files.add(candidate)

    for path in sorted(allowed_files, key=lambda item: str(item)):
        rel_path = path.relative_to(root).as_posix()
        text = _read_text(path)
        extractor = TextExtractor()
        extractor.feed(text)
        body = _clean_text(" ".join(extractor.paragraphs))
        title = extractor.headings[0] if extractor.headings else path.stem
        record: dict[str, Any] = {
            "title": title,
            "source_path": rel_path,
            "headings": extractor.headings[:12],
            "text_chars": len(body),
            "table_count": extractor.table_count,
        }
        if include_raw_text:
            limit = max(0, int(raw_text_chars or 0))
            record["text"] = body[:limit] if limit else body
        records.append(record)
    return records


def build_search_index(cards: list[dict[str, Any]]) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for card in cards:
        text = " ".join(
            [
                str(card.get("title") or ""),
                " ".join(card.get("aliases") or []),
                " ".join(card.get("tags") or []),
                str(card.get("summary") or ""),
            ]
        )
        index[str(card.get("id") or "")] = sorted(_tokens(text))
    return index


def _refresh_source_map(source_map: dict[str, Any], page_records: list[dict[str, Any]]) -> dict[str, Any]:
    known_paths = {record["source_path"] for record in page_records}
    refreshed: dict[str, Any] = {}
    for rule_id, refs in source_map.items():
        good_refs = []
        for ref in refs if isinstance(refs, list) else []:
            if not isinstance(ref, dict):
                continue
            path = str(ref.get("path") or "")
            good_refs.append({"book": str(ref.get("book") or "玩家手册2024"), "path": path, "available": path in known_paths})
        refreshed[str(rule_id)] = good_refs
    return refreshed


def _refresh_cards(cards: list[dict[str, Any]], source_map: dict[str, Any]) -> list[dict[str, Any]]:
    refreshed = []
    for card in cards:
        item = dict(card)
        refs = source_map.get(str(item.get("id") or ""))
        if refs:
            item["source_refs"] = [{"book": ref.get("book", ""), "path": ref.get("path", "")} for ref in refs]
        refreshed.append(item)
    return refreshed


def _review_warnings(cards: list[dict[str, Any]], page_records: list[dict[str, Any]], source_map: dict[str, Any]) -> list[str]:
    warnings = [
        "# DND 2024 Core Rulebook Build Warnings",
        "",
        "Runtime files intentionally avoid full rulebook text. Review summaries before treating them as authoritative.",
        "",
    ]
    known_paths = {record["source_path"] for record in page_records}
    for card in cards:
        for ref in source_map.get(str(card.get("id") or ""), []):
            path = str(ref.get("path") or "")
            if path and path not in known_paths:
                warnings.append(f"- Missing source page for `{card.get('id')}`: `{path}`")
    return warnings


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "gb18030", "mbcs"):
        try:
            return path.read_text(encoding=encoding)
        except LookupError:
            continue
        except UnicodeDecodeError:
            continue
        except OSError:
            return ""
    return ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return records
    for line in lines:
        if not line.strip():
            continue
        data = json.loads(line)
        if isinstance(data, dict):
            records.append(data)
    return records


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")


def _source_paths(cards: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for card in cards:
        for ref in card.get("source_refs") or []:
            if isinstance(ref, dict) and ref.get("path"):
                paths.add(str(ref["path"]))
    return paths


def _tokens(text: str) -> set[str]:
    normalized = text.lower()
    tokens = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", normalized))
    compact = re.sub(r"\s+", "", normalized)
    for size in (2, 3):
        for index in range(max(0, len(compact) - size + 1)):
            gram = compact[index : index + size]
            if re.search(r"[\u4e00-\u9fff]", gram):
                tokens.add(gram)
    return tokens


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


if __name__ == "__main__":
    raise SystemExit(main())

import json

from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository


def _write_jsonl(path, records):
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def test_last_audit_records_reads_across_rotated_files(tmp_path):
    repository = JsonGameRepository(tmp_path / "data", max_audit_backups=2)
    path = repository.audit_path("group")

    _write_jsonl(
        path.with_name(f"{path.name}.2"),
        [
            {"at": "2026-05-13T00:00:01+00:00", "message": "older-1"},
            {"at": "2026-05-13T00:00:02+00:00", "message": "older-2"},
        ],
    )
    _write_jsonl(
        path.with_name(f"{path.name}.1"),
        [
            {"at": "2026-05-13T00:00:03+00:00", "message": "rotated-1"},
            {"at": "2026-05-13T00:00:04+00:00", "message": "rotated-2"},
        ],
    )
    _write_jsonl(
        path,
        [
            {"at": "2026-05-13T00:00:05+00:00", "message": "current-1"},
            {"at": "2026-05-13T00:00:06+00:00", "message": "current-2"},
        ],
    )

    records = repository.last_audit_records("group", limit=4)

    assert [record["message"] for record in records] == [
        "rotated-1",
        "rotated-2",
        "current-1",
        "current-2",
    ]


def test_last_audit_records_skips_malformed_lines_across_rotated_files(tmp_path):
    repository = JsonGameRepository(tmp_path / "data", max_audit_backups=1)
    path = repository.audit_path("group")
    path.with_name(f"{path.name}.1").write_text(
        '{"message":"rotated"}\nnot json\n',
        encoding="utf-8",
    )
    path.write_text('{"message":"current"}\n', encoding="utf-8")

    records = repository.last_audit_records("group", limit=3)

    assert [record["message"] for record in records] == ["rotated", "current"]

import sqlite3

import pytest

from app import foreshadowing_store as store


@pytest.fixture
def isolated_store(monkeypatch, tmp_path):
    db_path = tmp_path / "foreshadowings.db"
    monkeypatch.setattr(store, "FORESHADOWING_DB_PATH", str(db_path))
    return db_path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (3, 3),
        ("12", 12),
        (None, None),
        ("", None),
        ("   ", None),
        (True, None),
        (False, None),
        (1.0, None),
        (-2, None),
        (0, None),
        ("-2", None),
        ("第二章", None),
        ("12.0", None),
    ],
)
def test_normalize_resolve_chapter(raw, expected):
    assert store.normalize_resolve_chapter(raw) == expected


def test_create_and_update_only_persist_int_or_null(isolated_store):
    created = store.create_foreshadowing({
        "task_id": "task-normalize",
        "name": "numeric string",
        "resolve_chapter": "7",
    })
    assert created["resolve_chapter"] == 7

    with sqlite3.connect(isolated_store) as conn:
        raw = conn.execute(
            "SELECT resolve_chapter, typeof(resolve_chapter) FROM foreshadowings WHERE id = ?",
            (created["id"],),
        ).fetchone()
    assert raw == (7, "integer")

    updated = store.update_foreshadowing(created["id"], {"resolve_chapter": "第二章"})
    assert updated["resolve_chapter"] is None
    with sqlite3.connect(isolated_store) as conn:
        raw = conn.execute(
            "SELECT resolve_chapter FROM foreshadowings WHERE id = ?", (created["id"],)
        ).fetchone()[0]
    assert raw is None


def test_invalid_write_warning_does_not_include_private_value(
    isolated_store, caplog
):
    private_value = "private chapter label"

    store.create_foreshadowing({
        "task_id": "task-redacted-warning",
        "name": "invalid value",
        "resolve_chapter": private_value,
    })

    assert private_value not in caplog.text
    assert "input_type=str" in caplog.text


def test_invalid_historical_values_do_not_break_summary_or_get_rewritten(isolated_store):
    store.create_foreshadowing({
        "id": "valid-int",
        "task_id": "task-history",
        "name": "valid",
        "resolve_chapter": 2,
        "status": "planted",
    })
    store.create_foreshadowing({
        "id": "valid-string",
        "task_id": "task-history",
        "name": "numeric string",
        "resolve_chapter": 3,
        "status": "planted",
    })

    # Simulate legacy rows that bypassed the normalized write path.
    with sqlite3.connect(isolated_store) as conn:
        conn.execute(
            "UPDATE foreshadowings SET resolve_chapter = ? WHERE id = ?",
            ("3", "valid-string"),
        )
        conn.execute(
            "INSERT INTO foreshadowings (id, task_id, name, resolve_chapter, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("invalid-string", "task-history", "invalid", "下一章", "planted"),
        )
        conn.commit()

    summary = store.get_foreshadowing_summary("task-history", current_chapter=3)
    assert summary["broken"] == 2
    assert summary["invalid_resolve_chapter_count"] == 1
    assert store.get_foreshadowing("invalid-string")["resolve_chapter"] is None

    # A read-only health check must not migrate or rewrite legacy data.
    with sqlite3.connect(isolated_store) as conn:
        raw_values = dict(conn.execute(
            "SELECT id, resolve_chapter FROM foreshadowings WHERE task_id = ?",
            ("task-history",),
        ).fetchall())
    assert raw_values["valid-string"] == 3  # SQLite affinity canonicalizes numeric text.
    assert raw_values["invalid-string"] == "下一章"


def test_empty_and_invalid_historical_values_are_skipped(isolated_store):
    store.create_foreshadowing({
        "id": "seed",
        "task_id": "task-invalid",
        "name": "seed",
        "resolve_chapter": None,
    })
    with sqlite3.connect(isolated_store) as conn:
        rows = [
            ("empty", ""),
            ("float", 2.5),
            ("negative", -1),
            ("zero", 0),
            ("text", "第二章"),
        ]
        for row_id, value in rows:
            conn.execute(
                "INSERT INTO foreshadowings (id, task_id, name, resolve_chapter) "
                "VALUES (?, ?, ?, ?)",
                (row_id, "task-invalid", row_id, value),
            )
        conn.commit()

    summary = store.get_foreshadowing_summary("task-invalid", current_chapter=10)
    assert summary["broken"] == 0
    assert summary["upcoming"] == 0
    assert summary["invalid_resolve_chapter_count"] == 4


def test_summary_normalizes_string_current_chapter(isolated_store):
    store.create_foreshadowing({
        "id": "due",
        "task_id": "task-string-current",
        "name": "due event",
        "resolve_chapter": 3,
        "status": "planted",
    })

    summary = store.get_foreshadowing_summary(
        "task-string-current", current_chapter="3"
    )

    assert summary["broken"] == 1
    assert summary["upcoming"] == 0


def test_invalid_current_chapter_skips_comparisons(isolated_store):
    store.create_foreshadowing({
        "id": "future",
        "task_id": "task-invalid-current",
        "name": "future event",
        "resolve_chapter": 3,
        "status": "planted",
    })

    summary = store.get_foreshadowing_summary(
        "task-invalid-current", current_chapter="chapter two"
    )

    assert summary["broken"] == 0
    assert summary["upcoming"] == 0


def test_active_and_unresolved_filters_normalize_legacy_values(isolated_store):
    store.create_foreshadowing({
        "id": "seed",
        "task_id": "task-filter",
        "name": "seed",
        "plant_chapter": 1,
        "resolve_chapter": None,
        "status": "planted",
    })
    with sqlite3.connect(isolated_store) as conn:
        conn.execute(
            "INSERT INTO foreshadowings "
            "(id, task_id, name, plant_chapter, resolve_chapter, status, importance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("valid", "task-filter", "valid", 1, "3", "planted", 8),
        )
        conn.execute(
            "INSERT INTO foreshadowings "
            "(id, task_id, name, plant_chapter, resolve_chapter, status, importance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("invalid", "task-filter", "invalid", 1, "later", "planted", 9),
        )
        conn.commit()

    active = store.get_active_for_chapter("task-filter", chapter="2")
    unresolved = store.get_unresolved_foreshadowings(
        "task-filter", current_chapter="3"
    )

    assert [row["id"] for row in active] == ["valid"]
    assert [row["id"] for row in unresolved] == ["valid"]
    with sqlite3.connect(isolated_store) as conn:
        raw = conn.execute(
            "SELECT resolve_chapter FROM foreshadowings WHERE id = ?",
            ("invalid",),
        ).fetchone()[0]
    assert raw == "later"

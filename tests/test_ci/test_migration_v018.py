"""V018 migration: persist metacognitive recovery execution results."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from opensquilla.persistence.migrator import apply_pending

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_v018_adds_metacognition_recovery_result_json_column(
    tmp_path: Path,
) -> None:
    db = str(tmp_path / "v018.db")
    applied = apply_pending(db, MIGRATIONS_DIR)
    assert "V018__meta_skill_runs_metacognition_recovery_result" in applied

    conn = sqlite3.connect(db)
    try:
        assert "metacognition_recovery_result_json" in _columns(
            conn,
            "meta_skill_runs",
        )
    finally:
        conn.close()

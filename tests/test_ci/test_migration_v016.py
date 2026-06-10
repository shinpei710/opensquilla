"""V016 migration: persist metacognitive completion decisions."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from opensquilla.persistence.migrator import apply_pending

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_v016_adds_metacognition_decision_json_column(tmp_path: Path) -> None:
    db = str(tmp_path / "v016.db")
    applied = apply_pending(db, MIGRATIONS_DIR)
    assert "V016__meta_skill_runs_metacognition_decision" in applied

    conn = sqlite3.connect(db)
    try:
        assert "metacognition_decision_json" in _columns(conn, "meta_skill_runs")
    finally:
        conn.close()

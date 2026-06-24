"""V015 - persist metacognitive reports for meta-skill runs.

V1 attached the report to the terminal ``MetaResult``. This migration adds a
nullable JSON column so operators can inspect the same reliability report from
``opensquilla skills meta runs show`` after the turn has finished.
"""

from __future__ import annotations

from yoyo import step

__depends__: set[str] = {"V014__meta_skill_run_steps_allow_user_input"}


def _table_exists(conn, table: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def _column_exists(conn, table: str, column: str) -> bool:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def apply_step(conn) -> None:
    if not _table_exists(conn, "meta_skill_runs"):
        return
    if _column_exists(conn, "meta_skill_runs", "metacognition_json"):
        return
    conn.cursor().execute(
        "ALTER TABLE meta_skill_runs ADD COLUMN metacognition_json TEXT"
    )


def rollback_step(conn) -> None:
    if not _table_exists(conn, "meta_skill_runs"):
        return
    if not _column_exists(conn, "meta_skill_runs", "metacognition_json"):
        return
    conn.cursor().execute("ALTER TABLE meta_skill_runs DROP COLUMN metacognition_json")


steps = [step(apply_step, rollback_step)]

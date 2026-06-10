"""V018 - persist metacognitive recovery execution results.

V017 stores advisory recovery plans. V018 adds a nullable JSON column for the
bounded action that the orchestrator actually attempted, starting with the
single permitted automatic action: regenerate_final_text.
"""

from __future__ import annotations

from yoyo import step

__depends__: set[str] = {"V017__meta_skill_runs_metacognition_recovery"}


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
    if _column_exists(
        conn,
        "meta_skill_runs",
        "metacognition_recovery_result_json",
    ):
        return
    conn.cursor().execute(
        "ALTER TABLE meta_skill_runs "
        "ADD COLUMN metacognition_recovery_result_json TEXT"
    )


def rollback_step(conn) -> None:
    if not _table_exists(conn, "meta_skill_runs"):
        return
    if not _column_exists(
        conn,
        "meta_skill_runs",
        "metacognition_recovery_result_json",
    ):
        return
    conn.cursor().execute(
        "ALTER TABLE meta_skill_runs DROP COLUMN metacognition_recovery_result_json"
    )


steps = [step(apply_step, rollback_step)]

"""CLI smoke tests for `opensquilla skills meta runs ...`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opensquilla.cli.main import app as cli_app
from opensquilla.persistence.meta_run_writer import open_meta_run_writer
from opensquilla.persistence.migrator import apply_pending
from opensquilla.skills.meta.types import MetaPlan, MetaResult, MetaStep

MIGRATIONS_DIR = Path(__file__).resolve().parents[1].parent / "migrations"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def seeded_db(tmp_path: Path, monkeypatch):
    db = str(tmp_path / "test.db")
    apply_pending(db, MIGRATIONS_DIR)
    w = open_meta_run_writer(db)

    plan_a = MetaPlan(
        name="alpha-skill", triggers=("t",), priority=10,
        steps=(MetaStep(id="s1", skill="x", kind="agent"),),
    )
    plan_b = MetaPlan(
        name="beta-skill", triggers=("t",), priority=10,
        steps=(MetaStep(id="s1", skill="y", kind="agent"),),
    )
    rid_ok = w.begin_run_sync(
        meta_skill_name="alpha-skill", meta_plan=plan_a,
        triggered_by="soft_meta_invoke", inputs={"user_message": "hi"},
        session_key="sess-1", turn_id="turn-1",
    )
    w.begin_step_sync(
        run_id=rid_ok, step=plan_a.steps[0], effective_skill="x",
        rendered_inputs={"a": 1},
    )
    w.finish_step_sync(
        run_id=rid_ok, step_id="s1", status="ok", output_text="alpha-out",
    )
    w.finish_run_sync(
        run_id=rid_ok, status="ok",
        result=MetaResult(
            ok=True,
            final_text="alpha-out",
            metacognition={
                "status": "warning",
                "summary": "The run completed with metacognitive warnings to inspect.",
                "plan": "alpha-skill",
                "state": {
                    "steps_total": 1,
                    "steps_started": 1,
                    "steps_finished": 1,
                    "steps_skipped": 0,
                    "steps_failed": 0,
                    "paused_step_id": None,
                },
                "completion_check": {
                    "ok": True,
                    "paused": False,
                    "final_text_present": True,
                    "step_outputs_present": True,
                    "failed_step_id": None,
                },
                "signals": [
                    {
                        "kind": "empty_step_output",
                        "severity": "warning",
                        "message": "Step completed successfully but produced no output.",
                        "step_id": "s1",
                        "details": {},
                    },
                ],
            },
            metacognition_recovery_result={
                "action": "regenerate_final_text",
                "status": "applied",
                "reason": "Final text was synthesized from captured step outputs.",
                "final_text_changed": True,
                "final_text_chars": 9,
            },
        ),
    )

    rid_fail = w.begin_run_sync(
        meta_skill_name="beta-skill", meta_plan=plan_b,
        triggered_by="hard_takeover", inputs={},
        session_key=None, turn_id=None,
    )
    w.finish_run_sync(
        run_id=rid_fail, status="failed",
        result=MetaResult(ok=False, error="boom", failed_step_id="s1"),
    )

    plan_wait = MetaPlan(
        name="wait-skill", triggers=("t",), priority=10,
        steps=(MetaStep(id="collect", skill="", kind="user_input"),),
    )
    rid_wait = w.begin_run_sync(
        meta_skill_name="wait-skill", meta_plan=plan_wait,
        triggered_by="soft_meta_invoke", inputs={"user_message": "need info"},
        session_key="sess-wait", turn_id="turn-wait",
    )
    wait_schema = {
        "mode": "form",
        "fields": [
            {
                "name": "destination",
                "type": "string",
                "required": True,
                "prompt": "Where should we go?",
            },
            {
                "name": "days",
                "type": "int",
                "required": True,
                "prompt": "How many days?",
                "min": 1,
                "max": 30,
            },
            {
                "name": "budget",
                "type": "enum",
                "required": False,
                "prompt": "Budget level?",
                "choices": ["low", "mid", "high"],
                "default": "mid",
            },
        ],
        "intro": "Need trip details.",
        "cancel_keywords": ["cancel"],
        "timeout_hours": 24,
    }
    assert w.try_claim_awaiting(
        run_id=rid_wait,
        step_id="collect",
        schema_json=json.dumps(wait_schema, sort_keys=True),
        session_id="sess-wait",
        inputs_json='{"user_message":"need info"}',
        step_outputs_json="{}",
        awaiting_since=1700000000.0,
    ) is True
    w.close()

    monkeypatch.setenv("OPENSQUILLA_META_RUNS_DB", db)
    return {
        "db": db,
        "rid_ok": rid_ok,
        "rid_fail": rid_fail,
        "rid_wait": rid_wait,
    }


def test_runs_list(runner: CliRunner, seeded_db) -> None:
    result = runner.invoke(cli_app, ["skills", "meta", "runs", "list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data) == 3


def test_runs_list_filter_status(runner: CliRunner, seeded_db) -> None:
    result = runner.invoke(
        cli_app,
        ["skills", "meta", "runs", "list", "--status", "failed", "--json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["status"] == "failed"


def test_runs_show(runner: CliRunner, seeded_db) -> None:
    result = runner.invoke(
        cli_app, ["skills", "meta", "runs", "show", seeded_db["rid_ok"], "--json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["meta_skill_name"] == "alpha-skill"
    assert data["status"] == "ok"
    assert data["metacognition"]["status"] == "warning"
    assert data["metacognition_decision"]["action"] == "warn"
    assert data["metacognition_recovery"]["primary_action"] == "deliver_with_warning"
    recovery_options = data["metacognition_recovery"]["options"]
    assert recovery_options[0]["execution"]["mode"] == "surface"
    assert recovery_options[1]["execution"]["mode"] == "manual"
    assert data["metacognition_recovery_result"]["status"] == "applied"
    assert "metacognition_json" not in data
    assert "metacognition_decision_json" not in data
    assert "metacognition_recovery_json" not in data
    assert "metacognition_recovery_result_json" not in data


def test_runs_show_text_includes_metacognition_summary(
    runner: CliRunner, seeded_db
) -> None:
    result = runner.invoke(
        cli_app, ["skills", "meta", "runs", "show", seeded_db["rid_ok"]],
    )
    assert result.exit_code == 0
    assert "metacognition: warning (warning=1)" in result.output
    assert "meta_summary:" in result.output
    assert "meta_decision: warn" in result.output
    assert "decision_reason:" in result.output
    assert "decision_next:" in result.output
    assert "recovery:      deliver_with_warning" in result.output
    assert (
        "recovery_opts: deliver_with_warning(surface:available), "
        "inspect_run(manual:manual_only)"
    ) in result.output
    assert "recovery_result: applied" in result.output
    assert "recovery_detail:" in result.output


def test_runs_steps(runner: CliRunner, seeded_db) -> None:
    result = runner.invoke(
        cli_app, ["skills", "meta", "runs", "steps", seeded_db["rid_ok"], "--json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["step_id"] == "s1"
    assert data[0]["status"] == "ok"


def test_runs_failures(runner: CliRunner, seeded_db) -> None:
    result = runner.invoke(cli_app, ["skills", "meta", "runs", "failures", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["status"] == "failed"


def test_runs_replay_dry_run(runner: CliRunner, seeded_db) -> None:
    """W8: --dry-run prints DAG in the spec'd format."""
    result = runner.invoke(
        cli_app,
        ["skills", "meta", "runs", "replay", seeded_db["rid_ok"], "--dry-run", "--json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["meta_skill_name"] == "alpha-skill"
    assert data["plan_source"] == "historical_snapshot"
    assert len(data["steps"]) == 1


def test_runs_recover_cancel_requires_confirm(
    runner: CliRunner,
    seeded_db,
) -> None:
    result = runner.invoke(
        cli_app,
        [
            "skills",
            "meta",
            "runs",
            "recover",
            seeded_db["rid_wait"],
            "--action",
            "cancel_run",
            "--json",
        ],
    )
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["status"] == "requires_confirmation"
    assert "Cancel this awaiting MetaSkill run" in data["confirmation_prompt"]

    w = open_meta_run_writer(seeded_db["db"])
    try:
        rec = w.get_run(seeded_db["rid_wait"])
    finally:
        w.close()
    assert rec is not None
    assert rec.status == "awaiting_user"


def test_runs_recover_cancel_awaiting_run(
    runner: CliRunner,
    seeded_db,
) -> None:
    result = runner.invoke(
        cli_app,
        [
            "skills",
            "meta",
            "runs",
            "recover",
            seeded_db["rid_wait"],
            "--action",
            "cancel_run",
            "--confirm",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["status"] == "cancelled"
    assert data["action"] == "cancel_run"

    w = open_meta_run_writer(seeded_db["db"])
    try:
        rec = w.get_run(seeded_db["rid_wait"])
    finally:
        w.close()
    assert rec is not None
    assert rec.status == "cancelled"
    assert rec.error is not None
    assert "cli_recover_cancel_run" in rec.error


def test_runs_recover_cancel_rejects_finished_run(
    runner: CliRunner,
    seeded_db,
) -> None:
    result = runner.invoke(
        cli_app,
        [
            "skills",
            "meta",
            "runs",
            "recover",
            seeded_db["rid_ok"],
            "--action",
            "cancel_run",
            "--confirm",
            "--json",
        ],
    )
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["status"] == "not_applicable"
    assert "awaiting_user" in data["reason"]


def test_runs_recover_resume_requires_confirm_and_keeps_run_awaiting(
    runner: CliRunner,
    seeded_db,
) -> None:
    result = runner.invoke(
        cli_app,
        [
            "skills",
            "meta",
            "runs",
            "recover",
            seeded_db["rid_wait"],
            "--action",
            "resume_after_user_input",
            "--fields-json",
            json.dumps({"destination": "Tokyo", "days": 5}),
            "--json",
        ],
    )
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["status"] == "requires_confirmation"
    assert data["gateway_required"] is True
    assert data["filled_fields"] == {"destination": "Tokyo", "days": 5}
    assert data["missing_fields"] == []
    assert "Resume this MetaSkill run" in data["confirmation_prompt"]

    w = open_meta_run_writer(seeded_db["db"])
    try:
        rec = w.get_run(seeded_db["rid_wait"])
    finally:
        w.close()
    assert rec is not None
    assert rec.status == "awaiting_user"


def test_runs_recover_resume_reports_missing_required_fields(
    runner: CliRunner,
    seeded_db,
) -> None:
    result = runner.invoke(
        cli_app,
        [
            "skills",
            "meta",
            "runs",
            "recover",
            seeded_db["rid_wait"],
            "--action",
            "resume_after_user_input",
            "--fields-json",
            json.dumps({"destination": "Tokyo"}),
            "--json",
        ],
    )
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["status"] == "validation_error"
    assert data["filled_fields"] == {"destination": "Tokyo"}
    assert data["missing_fields"] == ["days"]
    assert any("required field 'days'" in err for err in data["validation_errors"])


def test_runs_recover_resume_confirm_prepares_payload_without_claiming(
    runner: CliRunner,
    seeded_db,
) -> None:
    result = runner.invoke(
        cli_app,
        [
            "skills",
            "meta",
            "runs",
            "recover",
            seeded_db["rid_wait"],
            "--action",
            "resume_after_user_input",
            "--fields-json",
            json.dumps({"destination": "Tokyo", "days": 5, "budget": "high"}),
            "--confirm",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["status"] == "prepared"
    assert data["gateway_required"] is True
    assert data["filled_fields"] == {
        "destination": "Tokyo",
        "days": 5,
        "budget": "high",
    }
    assert data["validation_errors"] == []

    w = open_meta_run_writer(seeded_db["db"])
    try:
        rec = w.get_run(seeded_db["rid_wait"])
    finally:
        w.close()
    assert rec is not None
    assert rec.status == "awaiting_user"


def test_runs_recover_resume_rejects_finished_run(
    runner: CliRunner,
    seeded_db,
) -> None:
    result = runner.invoke(
        cli_app,
        [
            "skills",
            "meta",
            "runs",
            "recover",
            seeded_db["rid_ok"],
            "--action",
            "resume_after_user_input",
            "--fields-json",
            json.dumps({"destination": "Tokyo", "days": 5}),
            "--confirm",
            "--json",
        ],
    )
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["status"] == "not_applicable"
    assert "awaiting_user" in data["reason"]


def test_runs_recover_reports_unsupported_action(
    runner: CliRunner,
    seeded_db,
) -> None:
    result = runner.invoke(
        cli_app,
        [
            "skills",
            "meta",
            "runs",
            "recover",
            seeded_db["rid_fail"],
            "--action",
            "retry_run",
            "--confirm",
            "--json",
        ],
    )
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert data["status"] == "unsupported"
    assert data["action"] == "retry_run"


def test_runs_show_bad_id(runner: CliRunner, seeded_db) -> None:
    result = runner.invoke(cli_app, ["skills", "meta", "runs", "show", "BOGUS", "--json"])
    assert result.exit_code != 0


def test_runs_list_empty(runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
    db = str(tmp_path / "empty.db")
    apply_pending(db, MIGRATIONS_DIR)
    monkeypatch.setenv("OPENSQUILLA_META_RUNS_DB", db)
    result = runner.invoke(cli_app, ["skills", "meta", "runs", "list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == []

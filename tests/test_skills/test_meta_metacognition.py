"""Metacognitive monitoring for MetaSkill DAG runs."""

from __future__ import annotations

import pytest

from opensquilla.skills.meta.events import _StepDone
from opensquilla.skills.meta.metacognition import (
    MetacognitiveController,
    decide_completion,
    refresh_report_final_text,
)
from opensquilla.skills.meta.scheduler import run_dag
from opensquilla.skills.meta.types import MetaMatch, MetaPlan, MetaResult, MetaStep


async def _yield_skill_view(step_id: str, skill_name: str):
    return
    yield  # type: ignore[unreachable]


def _single_step_plan() -> MetaPlan:
    return MetaPlan(
        name="meta-metacognition-test",
        triggers=("test",),
        priority=10,
        steps=(MetaStep(id="draft", skill="writer", kind="agent"),),
    )


async def _run_with_controller(dispatch):
    final: MetaResult | None = None
    async for item in run_dag(
        MetaMatch(plan=_single_step_plan(), inputs={"user_message": "test"}),
        dispatch_step_stream=dispatch,
        yield_skill_view_preface=_yield_skill_view,
        metacognition_controller=MetacognitiveController(),
    ):
        if isinstance(item, MetaResult):
            final = item
    assert final is not None
    assert final.metacognition is not None
    return final


@pytest.mark.asyncio
async def test_metacognition_report_passes_clean_success() -> None:
    async def dispatch(step, effective_skill, inputs, outputs):
        yield _StepDone(text="deliverable")

    final = await _run_with_controller(dispatch)

    assert final.ok is True
    assert final.metacognition["status"] == "passed"
    assert final.metacognition["state"]["steps_total"] == 1
    assert final.metacognition["state"]["steps_finished"] == 1
    assert final.metacognition["completion_check"]["final_text_present"] is True
    assert final.metacognition_decision is not None
    assert final.metacognition_decision["action"] == "pass"


@pytest.mark.asyncio
async def test_metacognition_warns_on_empty_success_output() -> None:
    async def dispatch(step, effective_skill, inputs, outputs):
        yield _StepDone(text="")

    final = await _run_with_controller(dispatch)

    assert final.ok is True
    assert final.metacognition["status"] == "warning"
    signal_kinds = {signal["kind"] for signal in final.metacognition["signals"]}
    assert "empty_step_output" in signal_kinds
    assert "empty_final_text" in signal_kinds
    assert final.metacognition_decision is not None
    assert final.metacognition_decision["action"] == "block"


@pytest.mark.asyncio
async def test_metacognition_blocks_failed_run() -> None:
    async def dispatch(step, effective_skill, inputs, outputs):
        raise RuntimeError("boom")
        yield _StepDone(text="unreachable")  # type: ignore[unreachable]

    final = await _run_with_controller(dispatch)

    assert final.ok is False
    assert final.metacognition["status"] == "blocked"
    signal_kinds = {signal["kind"] for signal in final.metacognition["signals"]}
    assert "step_failed" in signal_kinds
    assert "run_failed" in signal_kinds
    assert final.metacognition_decision is not None
    assert final.metacognition_decision["action"] == "block"


def test_refresh_report_final_text_clears_empty_final_warning() -> None:
    report = {
        "status": "warning",
        "summary": "warning",
        "completion_check": {"final_text_present": False},
        "signals": [
            {
                "kind": "empty_final_text",
                "severity": "warning",
                "message": "empty",
                "step_id": None,
                "details": {},
            },
        ],
    }

    refreshed = refresh_report_final_text(report, "post-processed answer")

    assert refreshed is report
    assert report["status"] == "passed"
    assert report["completion_check"]["final_text_present"] is True
    assert report["signals"] == []
    decision = decide_completion(report)
    assert decision is not None
    assert decision["action"] == "pass"


def test_decide_completion_warns_when_deliverable_has_warnings() -> None:
    report = {
        "status": "warning",
        "summary": "warning",
        "completion_check": {
            "ok": True,
            "paused": False,
            "final_text_present": True,
            "step_outputs_present": True,
        },
        "signals": [
            {
                "kind": "step_failover",
                "severity": "warning",
                "message": "failover",
                "step_id": "draft",
                "details": {},
            },
        ],
    }

    decision = decide_completion(report)

    assert decision is not None
    assert decision["action"] == "warn"
    assert "Deliver with the warning" in decision["suggested_next_step"]


def test_decide_completion_marks_pause_needs_review() -> None:
    report = {
        "status": "warning",
        "summary": "paused",
        "completion_check": {
            "ok": False,
            "paused": True,
            "final_text_present": False,
            "step_outputs_present": True,
        },
        "signals": [
            {
                "kind": "run_paused",
                "severity": "warning",
                "message": "paused",
                "step_id": "collect",
                "details": {},
            },
        ],
    }

    decision = decide_completion(report)

    assert decision is not None
    assert decision["action"] == "needs_review"

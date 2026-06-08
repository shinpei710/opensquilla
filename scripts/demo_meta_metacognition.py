#!/usr/bin/env python3
"""Run a tiny MetaSkill DAG and print the metacognition report.

This demo is intentionally local-only: it does not call an LLM provider,
gateway, browser, or external API. It exercises the MetaSkill scheduler path
where the metacognitive controller attaches reliability evidence to the
terminal MetaResult.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from opensquilla.engine.types import AgentEvent
from opensquilla.skills.meta.events import _StepDone
from opensquilla.skills.meta.metacognition import (
    MetacognitiveController,
    decide_completion,
    format_decision_notice,
)
from opensquilla.skills.meta.scheduler import run_dag
from opensquilla.skills.meta.types import MetaMatch, MetaPlan, MetaResult, MetaStep


def _build_plan() -> MetaPlan:
    return MetaPlan(
        name="demo-metacognition",
        triggers=("demo",),
        priority=10,
        steps=(MetaStep(id="draft", skill="demo-writer", kind="agent"),),
    )


async def _yield_skill_view(
    step_id: str,
    effective_skill: str,
) -> AsyncIterator[AgentEvent]:
    return
    yield  # type: ignore[unreachable]


def _dispatch_for_scenario(scenario: str):
    async def dispatch(
        step: MetaStep,
        effective_skill: str,
        inputs: dict[str, Any],
        outputs: dict[str, str],
    ) -> AsyncIterator[AgentEvent | _StepDone]:
        if scenario == "success":
            yield _StepDone(text="demo deliverable")
            return
        if scenario == "warning":
            yield _StepDone(text="")
            return
        if scenario == "failure":
            raise RuntimeError("demo step failed")
        raise RuntimeError(f"unknown scenario: {scenario}")

    return dispatch


async def _run_scenario(scenario: str) -> dict[str, Any]:
    final: MetaResult | None = None
    async for item in run_dag(
        MetaMatch(
            plan=_build_plan(),
            inputs={"user_message": f"run metacognition demo: {scenario}"},
        ),
        dispatch_step_stream=_dispatch_for_scenario(scenario),
        yield_skill_view_preface=_yield_skill_view,
        metacognition_controller=MetacognitiveController(),
    ):
        if isinstance(item, MetaResult):
            final = item
    if final is None:
        raise RuntimeError("demo run did not produce a MetaResult")
    decision = final.metacognition_decision or decide_completion(final.metacognition)
    return {
        "scenario": scenario,
        "ok": final.ok,
        "final_text": final.final_text,
        "error": final.error,
        "metacognition": final.metacognition,
        "metacognition_decision": decision,
        "tool_result_notice": format_decision_notice(decision),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "scenario",
        nargs="?",
        choices=("success", "warning", "failure"),
        default="success",
        help="Demo path to run.",
    )
    args = parser.parse_args()
    report = await _run_scenario(args.scenario)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

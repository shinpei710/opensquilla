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
    format_recovery_notice,
    format_recovery_result_notice,
    plan_recovery,
)
from opensquilla.skills.meta.orchestrator import MetaOrchestrator
from opensquilla.skills.meta.scheduler import run_dag
from opensquilla.skills.meta.types import MetaMatch, MetaPlan, MetaResult, MetaStep


def _build_plan() -> MetaPlan:
    return MetaPlan(
        name="demo-metacognition",
        triggers=("demo",),
        priority=10,
        steps=(MetaStep(id="draft", skill="demo-writer", kind="agent"),),
    )


def _build_recover_plan() -> MetaPlan:
    return MetaPlan(
        name="demo-metacognition-recover",
        triggers=("demo",),
        priority=10,
        steps=(
            MetaStep(
                id="research",
                skill="",
                kind="llm_chat",
                with_args={"task": "Produce intermediate demo material."},
            ),
            MetaStep(
                id="final",
                skill="",
                kind="llm_chat",
                depends_on=("research",),
                with_args={"task": "Return an intentionally empty final answer."},
            ),
        ),
        final_text_mode="raw",
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


async def _run_recover_scenario() -> MetaResult:
    calls = {"n": 0}

    async def fake_chat(_system_prompt: str, _user_message: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return "usable demo material from an intermediate step"
        if calls["n"] == 2:
            return ""
        return "Recovered demo deliverable."

    async def unused_runner(*_args: Any, **_kwargs: Any) -> AsyncIterator[AgentEvent]:
        raise AssertionError("recover demo should use llm_chat steps")
        yield  # type: ignore[unreachable]

    orch = MetaOrchestrator(
        agent_runner=unused_runner,
        skill_loader=None,
        llm_chat=fake_chat,
    )
    return await orch.run(
        MetaMatch(
            plan=_build_recover_plan(),
            inputs={"user_message": "run metacognition demo: recover"},
        ),
    )


async def _run_scenario(scenario: str) -> dict[str, Any]:
    if scenario == "recover":
        final_result = await _run_recover_scenario()
    else:
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
        final_result = final
    decision = final_result.metacognition_decision or decide_completion(
        final_result.metacognition,
    )
    recovery = final_result.metacognition_recovery or plan_recovery(
        final_result.metacognition,
        decision,
    )
    return {
        "scenario": scenario,
        "ok": final_result.ok,
        "final_text": final_result.final_text,
        "error": final_result.error,
        "metacognition": final_result.metacognition,
        "metacognition_decision": decision,
        "metacognition_recovery": recovery,
        "metacognition_recovery_result": (
            final_result.metacognition_recovery_result
        ),
        "tool_result_notice": "\n".join(
            part for part in (
                format_decision_notice(decision),
                format_recovery_notice(recovery),
                format_recovery_result_notice(
                    final_result.metacognition_recovery_result,
                ),
            )
            if part
        ),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "scenario",
        nargs="?",
        choices=("success", "warning", "failure", "recover"),
        default="success",
        help="Demo path to run.",
    )
    args = parser.parse_args()
    report = await _run_scenario(args.scenario)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

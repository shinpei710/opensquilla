"""Metacognitive monitoring for MetaSkill runs.

The controller models the run, records reliability signals, attaches a
completion report, and exposes a bounded recovery contract. Runtime
interventions stay deliberately narrow: only explicitly automatic recovery
actions may run without user confirmation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from opensquilla.skills.meta.types import MetaMatch, MetaPaused, MetaStep

Severity = Literal["info", "warning", "blocked"]
ReportStatus = Literal["passed", "warning", "blocked"]
DecisionAction = Literal["pass", "warn", "block", "needs_review"]
RecoveryAction = Literal[
    "none",
    "deliver_with_warning",
    "regenerate_final_text",
    "collect_user_input",
    "retry_or_fallback",
    "inspect_run",
]
RecoveryExecutionMode = Literal["automatic", "confirm", "manual", "surface", "none"]
RecoveryExecutionState = Literal[
    "available",
    "requires_confirmation",
    "manual_only",
    "not_needed",
    "applied",
    "skipped",
    "failed",
]
_SEVERITIES: tuple[Severity, ...] = ("info", "warning", "blocked")


@dataclass(frozen=True)
class MetacognitiveSignal:
    """One reliability signal observed while a MetaSkill run executes."""

    kind: str
    severity: Severity
    message: str
    step_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MetacognitiveDecision:
    """Completion-gate decision derived from a metacognition report."""

    action: DecisionAction
    reason: str
    surface_notice: str
    suggested_next_step: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MetacognitiveRecoveryOption:
    """One controlled recovery option for a metacognitive decision."""

    id: str
    label: str
    description: str
    requires_user_confirmation: bool = True
    automatic: bool = False
    execution: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MetacognitiveRecoveryPlan:
    """Machine-readable recovery plan with explicit execution boundaries."""

    primary_action: RecoveryAction
    reason: str
    options: list[dict[str, Any]]
    automatic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MetacognitiveController:
    """Lightweight state model and completion checker for one MetaSkill run."""

    def __init__(self) -> None:
        self._signals: list[MetacognitiveSignal] = []
        self._plan_name = ""
        self._steps_total = 0
        self._started_steps: set[str] = set()
        self._finished_steps: set[str] = set()
        self._skipped_steps: set[str] = set()
        self._failed_steps: set[str] = set()
        self._paused_step_id: str | None = None
        self._failovers: list[dict[str, str]] = []

    def start_run(self, match: MetaMatch, ordered_steps: list[MetaStep]) -> None:
        """Capture the static run frame before scheduling begins."""

        self._plan_name = match.plan.name
        self._steps_total = len(ordered_steps)
        if not match.inputs.get("user_message"):
            self._record(
                "missing_user_message",
                "warning",
                "MetaSkill run started without a user_message input.",
            )
        if not ordered_steps:
            self._record(
                "empty_plan",
                "blocked",
                "MetaSkill plan has no executable steps.",
            )

    def before_step(
        self,
        step: MetaStep,
        effective_skill: str,
        rendered_inputs: dict[str, Any],
        outputs: dict[str, str],
    ) -> None:
        """Observe the state immediately before a step starts."""

        self._started_steps.add(step.id)
        missing_deps = [dep for dep in step.depends_on if dep not in outputs]
        if missing_deps:
            self._record(
                "missing_dependency_output",
                "warning",
                "Step is starting before all declared dependency outputs are visible.",
                step_id=step.id,
                details={"missing": missing_deps},
            )
        if step.kind in {"agent", "skill_exec"} and not effective_skill:
            self._record(
                "missing_effective_skill",
                "blocked",
                "Step requires a skill but no effective skill was resolved.",
                step_id=step.id,
            )
        if step.with_args and not rendered_inputs:
            self._record(
                "empty_rendered_inputs",
                "warning",
                "Step declared input templates but rendered to an empty mapping.",
                step_id=step.id,
            )

    def record_skip(self, step: MetaStep, *, reason: str) -> None:
        self._skipped_steps.add(step.id)
        self._finished_steps.add(step.id)
        self._record(
            "step_skipped",
            "info",
            "Step was skipped by its condition.",
            step_id=step.id,
            details={"reason": reason, "kind": step.kind},
        )

    def after_step(
        self,
        step: MetaStep,
        effective_skill: str,
        output_text: str,
        *,
        status: str = "ok",
    ) -> None:
        self._finished_steps.add(step.id)
        if status == "failed":
            self._failed_steps.add(step.id)
        if status == "ok" and not output_text.strip() and step.kind != "user_input":
            self._record(
                "empty_step_output",
                "warning",
                "Step completed successfully but produced no output.",
                step_id=step.id,
                details={"kind": step.kind, "skill": effective_skill},
            )

    def record_failure(
        self,
        step: MetaStep,
        error: str,
        *,
        has_substitute: bool,
    ) -> None:
        self._failed_steps.add(step.id)
        severity: Severity = "warning" if has_substitute else "blocked"
        self._record(
            "step_failed",
            severity,
            "Step failed during execution.",
            step_id=step.id,
            details={"error": error, "has_substitute": has_substitute},
        )

    def record_failover(
        self,
        *,
        failed_step_id: str,
        substitute_step_id: str,
        error: str,
    ) -> None:
        self._failovers.append(
            {
                "failed_step_id": failed_step_id,
                "substitute_step_id": substitute_step_id,
                "error": error,
            },
        )
        self._record(
            "step_failover",
            "warning",
            "Step failure was routed to a substitute step.",
            step_id=failed_step_id,
            details={
                "substitute_step_id": substitute_step_id,
                "error": error,
            },
        )

    def record_pause(self, paused: MetaPaused) -> None:
        self._paused_step_id = paused.step_id
        self._record(
            "run_paused",
            "warning",
            "MetaSkill paused to collect user input.",
            step_id=paused.step_id,
            details={"run_id": paused.run_id},
        )

    def complete(
        self,
        *,
        ok: bool,
        final_text: str,
        step_outputs: dict[str, str],
        error: str | None = None,
        failed_step_id: str | None = None,
        paused: bool = False,
    ) -> dict[str, Any]:
        """Return a serialisable report for the terminal MetaResult."""

        if ok and not final_text.strip():
            self._record(
                "empty_final_text",
                "warning",
                "Run completed successfully but produced empty final_text.",
            )
        if ok and not step_outputs and self._steps_total:
            self._record(
                "missing_step_outputs",
                "warning",
                "Run completed successfully but no step outputs were captured.",
            )
        if not ok and not paused:
            self._record(
                "run_failed",
                "blocked",
                "Run did not complete successfully.",
                step_id=failed_step_id,
                details={"error": error or ""},
            )

        status: ReportStatus = "passed"
        if any(signal.severity == "blocked" for signal in self._signals):
            status = "blocked"
        elif any(signal.severity == "warning" for signal in self._signals):
            status = "warning"

        return {
            "status": status,
            "summary": self._summary_for_status(status),
            "plan": self._plan_name,
            "state": {
                "steps_total": self._steps_total,
                "steps_started": len(self._started_steps),
                "steps_finished": len(self._finished_steps),
                "steps_skipped": len(self._skipped_steps),
                "steps_failed": len(self._failed_steps),
                "paused_step_id": self._paused_step_id,
                "failovers": list(self._failovers),
            },
            "completion_check": {
                "ok": ok,
                "paused": paused,
                "final_text_present": bool(final_text.strip()),
                "step_outputs_present": bool(step_outputs),
                "failed_step_id": failed_step_id,
            },
            "signals": [signal.to_dict() for signal in self._signals],
        }

    def _record(
        self,
        kind: str,
        severity: Severity,
        message: str,
        *,
        step_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._signals.append(
            MetacognitiveSignal(
                kind=kind,
                severity=severity,
                message=message,
                step_id=step_id,
                details=details or {},
            ),
        )

    @staticmethod
    def _summary_for_status(status: ReportStatus) -> str:
        if status == "passed":
            return "No reliability warnings were detected for this MetaSkill run."
        if status == "warning":
            return "The run completed with metacognitive warnings to inspect."
        return "The run has blocked reliability signals and should not be trusted blindly."


def refresh_report_final_text(
    report: dict[str, Any] | None,
    final_text: str,
) -> dict[str, Any] | None:
    """Refresh completion evidence after orchestrator final-text post-processing."""

    if report is None:
        return None
    completion = report.setdefault("completion_check", {})
    final_text_present = bool(final_text.strip())
    completion["final_text_present"] = final_text_present
    if final_text_present:
        signals = [
            signal for signal in report.get("signals", [])
            if signal.get("kind") != "empty_final_text"
        ]
        report["signals"] = signals
        if any(signal.get("severity") == "blocked" for signal in signals):
            status: ReportStatus = "blocked"
        elif any(signal.get("severity") == "warning" for signal in signals):
            status = "warning"
        else:
            status = "passed"
        report["status"] = status
        report["summary"] = MetacognitiveController._summary_for_status(status)
    return report


def summarize_report(report: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a compact, surface-friendly summary of a metacognition report."""

    if not report:
        return None
    signals = [
        signal for signal in report.get("signals", [])
        if isinstance(signal, dict)
    ]
    signal_counts = {severity: 0 for severity in _SEVERITIES}
    for signal in signals:
        severity = signal.get("severity")
        if severity in signal_counts:
            signal_counts[severity] += 1

    state = report.get("state", {})
    if not isinstance(state, dict):
        state = {}
    completion = report.get("completion_check", {})
    if not isinstance(completion, dict):
        completion = {}

    return {
        "status": str(report.get("status") or "passed"),
        "summary": str(report.get("summary") or ""),
        "plan": str(report.get("plan") or ""),
        "signal_counts": signal_counts,
        "state": {
            "steps_total": state.get("steps_total", 0),
            "steps_started": state.get("steps_started", 0),
            "steps_finished": state.get("steps_finished", 0),
            "steps_skipped": state.get("steps_skipped", 0),
            "steps_failed": state.get("steps_failed", 0),
            "paused_step_id": state.get("paused_step_id"),
        },
        "completion_check": {
            "ok": completion.get("ok"),
            "paused": completion.get("paused"),
            "final_text_present": completion.get("final_text_present"),
            "step_outputs_present": completion.get("step_outputs_present"),
            "failed_step_id": completion.get("failed_step_id"),
        },
    }


def decide_completion(report: dict[str, Any] | None) -> dict[str, Any] | None:
    """Decide whether a MetaSkill result can be treated as complete.

    This is a conservative completion gate, not an auto-repair policy. It
    converts observational evidence into a small decision contract that
    surfaces can act on without re-reading every signal.
    """

    summary = summarize_report(report)
    if summary is None:
        return None

    action: DecisionAction
    reason: str
    suggested_next_step: str
    completion = summary["completion_check"]
    status = summary["status"]

    if completion.get("paused") is True:
        action = "needs_review"
        reason = "MetaSkill paused before completion and is waiting for user input."
        suggested_next_step = "Collect the requested user input, then resume the run."
    elif status == "blocked" or completion.get("ok") is False:
        action = "block"
        reason = "Blocked metacognitive reliability signals were detected."
        suggested_next_step = (
            "Do not treat the output as final; inspect the run and retry or "
            "fall back."
        )
    elif completion.get("final_text_present") is False:
        action = "block"
        reason = "MetaSkill reported success but produced no user-facing final text."
        suggested_next_step = (
            "Regenerate the final response or inspect the step outputs before "
            "delivery."
        )
    elif completion.get("step_outputs_present") is False:
        action = "block"
        reason = "MetaSkill reported success but no step outputs were captured."
        suggested_next_step = "Inspect the run trace before trusting this result."
    elif status == "warning":
        action = "warn"
        reason = "Metacognitive warnings were detected, but the run produced a deliverable."
        suggested_next_step = (
            "Deliver with the warning visible, or review the run trace if "
            "stakes are high."
        )
    else:
        action = "pass"
        reason = "No metacognitive completion issues were detected."
        suggested_next_step = "Deliver the MetaSkill result normally."

    return MetacognitiveDecision(
        action=action,
        reason=reason,
        surface_notice=_decision_notice(
            action=action,
            reason=reason,
            suggested_next_step=suggested_next_step,
            summary=summary,
        ),
        suggested_next_step=suggested_next_step,
    ).to_dict()


def format_decision_notice(decision: dict[str, Any] | None) -> str:
    """Return the user/tool-facing notice for non-pass decisions."""

    if not decision or decision.get("action") == "pass":
        return ""
    notice = str(decision.get("surface_notice") or "").strip()
    if notice:
        return notice
    action = str(decision.get("action") or "unknown")
    reason = str(decision.get("reason") or "Metacognitive decision requires attention.")
    next_step = str(decision.get("suggested_next_step") or "")
    suffix = f" Suggested next step: {next_step}" if next_step else ""
    return f"Metacognitive decision: {action}. {reason}{suffix}"


def plan_recovery(
    report: dict[str, Any] | None,
    decision: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Translate a completion decision into controlled recovery options."""

    if not decision:
        return None
    summary = summarize_report(report)
    completion = summary["completion_check"] if summary is not None else {}
    action = str(decision.get("action") or "pass")

    if action == "pass":
        return MetacognitiveRecoveryPlan(
            primary_action="none",
            reason="No recovery is needed.",
            options=[],
        ).to_dict()

    if action == "warn":
        return MetacognitiveRecoveryPlan(
            primary_action="deliver_with_warning",
            reason="A deliverable exists, but metacognitive warnings should remain visible.",
            options=[
                _recovery_option(
                    "deliver_with_warning",
                    "Deliver With Warning",
                    "Keep the deliverable and surface the warning notice.",
                    requires_user_confirmation=False,
                ),
                _recovery_option(
                    "inspect_run",
                    "Inspect Run",
                    "Review the persisted MetaSkill run trace before relying on it.",
                ),
            ],
        ).to_dict()

    if action == "needs_review":
        return MetacognitiveRecoveryPlan(
            primary_action="collect_user_input",
            reason="The run paused and needs user input before it can complete.",
            options=[
                _recovery_option(
                    "resume_after_user_input",
                    "Resume After Input",
                    "Collect the requested fields and resume the awaiting MetaSkill run.",
                ),
                _recovery_option(
                    "cancel_run",
                    "Cancel Run",
                    "Cancel the awaiting MetaSkill run instead of resuming it.",
                ),
                _recovery_option(
                    "inspect_run",
                    "Inspect Run",
                    "Review the run trace and awaiting form payload.",
                ),
            ],
        ).to_dict()

    if completion.get("final_text_present") is False and completion.get(
        "step_outputs_present",
    ) is True:
        return MetacognitiveRecoveryPlan(
            primary_action="regenerate_final_text",
            reason="The run has step outputs but no user-facing final text.",
            options=[
                _recovery_option(
                    "regenerate_final_text",
                    "Regenerate Final Text",
                    "Ask the model to synthesize a final answer from captured step outputs.",
                    requires_user_confirmation=False,
                    automatic=True,
                ),
                _recovery_option(
                    "inspect_run",
                    "Inspect Run",
                    "Review the step outputs and metacognitive signals.",
                ),
                _recovery_option(
                    "fallback_to_normal_turn",
                    "Fallback To Normal Turn",
                    "Let the parent agent continue with the blocked MetaSkill context.",
                ),
            ],
            automatic=True,
        ).to_dict()

    if completion.get("failed_step_id"):
        return MetacognitiveRecoveryPlan(
            primary_action="retry_or_fallback",
            reason="The run failed or was blocked around a specific step.",
            options=[
                _recovery_option(
                    "inspect_failed_step",
                    "Inspect Failed Step",
                    "Review the failed step, partial outputs, and error details.",
                ),
                _recovery_option(
                    "retry_run",
                    "Retry Run",
                    "Retry the MetaSkill after addressing the failed step.",
                ),
                _recovery_option(
                    "fallback_to_normal_turn",
                    "Fallback To Normal Turn",
                    "Let the parent agent continue with the failure context.",
                ),
            ],
        ).to_dict()

    return MetacognitiveRecoveryPlan(
        primary_action="inspect_run",
        reason="The completion gate blocked the result and no narrower recovery matched.",
        options=[
            _recovery_option(
                "inspect_run",
                "Inspect Run",
                "Review the run trace, signals, and captured outputs.",
            ),
            _recovery_option(
                "fallback_to_normal_turn",
                "Fallback To Normal Turn",
                "Let the parent agent continue with the blocked MetaSkill context.",
            ),
        ],
    ).to_dict()


def format_recovery_notice(recovery: dict[str, Any] | None) -> str:
    """Return a compact notice for controlled recovery options."""

    if not recovery or recovery.get("primary_action") == "none":
        return ""
    primary = str(recovery.get("primary_action") or "inspect_run")
    options = [
        format_recovery_option_brief(option)
        for option in recovery.get("options", [])
        if isinstance(option, dict) and option.get("id")
    ]
    options_text = ", ".join(options) if options else "none"
    automatic = str(bool(recovery.get("automatic"))).lower()
    reason = str(recovery.get("reason") or "").strip()
    reason_text = f" {reason}" if reason else ""
    return (
        f"Metacognitive recovery: primary={primary}; "
        f"options={options_text}; automatic={automatic}.{reason_text}"
    )


def format_recovery_option_brief(option: dict[str, Any]) -> str:
    """Return one recovery option with its V6 execution state."""

    option_id = str(option.get("id") or "unknown")
    execution = option.get("execution")
    if not isinstance(execution, dict):
        return option_id
    mode = str(execution.get("mode") or "").strip()
    state = str(execution.get("state") or "").strip()
    if mode and state:
        return f"{option_id}({mode}:{state})"
    if mode:
        return f"{option_id}({mode})"
    if state:
        return f"{option_id}({state})"
    return option_id


def format_recovery_result_notice(result: dict[str, Any] | None) -> str:
    """Return a compact notice for a recovery action that was actually tried."""

    if not result:
        return ""
    action = str(result.get("action") or "unknown")
    status = str(result.get("status") or "unknown")
    reason = str(result.get("reason") or "").strip()
    changed = result.get("final_text_changed")
    chars = result.get("final_text_chars")
    parts = [
        f"Metacognitive recovery result: action={action}; status={status}",
    ]
    if changed is not None:
        parts.append(f"final_text_changed={str(bool(changed)).lower()}")
    if isinstance(chars, int):
        parts.append(f"final_text_chars={chars}")
    notice = "; ".join(parts) + "."
    if reason:
        notice = f"{notice} {reason}"
    return notice


def annotate_recovery_with_result(
    recovery: dict[str, Any] | None,
    result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Attach a last-attempt record to the matching recovery option."""

    if not recovery or not result:
        return recovery
    action = str(result.get("action") or "")
    status = str(result.get("status") or "")
    if not action or not status:
        return recovery
    for option in recovery.get("options", []):
        if not isinstance(option, dict) or option.get("id") != action:
            continue
        execution = option.setdefault("execution", {})
        if not isinstance(execution, dict):
            execution = {}
            option["execution"] = execution
        if status in {"applied", "skipped", "failed"}:
            execution["state"] = status
        execution["last_status"] = status
        reason = str(result.get("reason") or "").strip()
        if reason:
            execution["last_reason"] = reason
        changed = result.get("final_text_changed")
        if changed is not None:
            execution["final_text_changed"] = bool(changed)
        chars = result.get("final_text_chars")
        if isinstance(chars, int):
            execution["final_text_chars"] = chars
        break
    return recovery


def _recovery_option(
    option_id: str,
    label: str,
    description: str,
    *,
    requires_user_confirmation: bool = True,
    automatic: bool = False,
) -> dict[str, Any]:
    if option_id in {"inspect_run", "inspect_failed_step"}:
        requires_user_confirmation = False
    execution = _recovery_execution_contract(
        option_id,
        requires_user_confirmation=requires_user_confirmation,
        automatic=automatic,
    )
    return MetacognitiveRecoveryOption(
        id=option_id,
        label=label,
        description=description,
        requires_user_confirmation=requires_user_confirmation,
        automatic=automatic,
        execution=execution,
    ).to_dict()


def _recovery_execution_contract(
    option_id: str,
    *,
    requires_user_confirmation: bool,
    automatic: bool,
) -> dict[str, Any]:
    if automatic:
        return {
            "mode": "automatic",
            "state": "available",
            "confirmation_required": False,
            "confirmation_prompt": "",
            "operator_hint": "Runs only inside the orchestrator's bounded recovery policy.",
        }
    if option_id == "deliver_with_warning":
        return {
            "mode": "surface",
            "state": "available",
            "confirmation_required": False,
            "confirmation_prompt": "",
            "operator_hint": "Surface the deliverable with the metacognitive warning visible.",
        }
    if option_id in {"inspect_run", "inspect_failed_step"}:
        return {
            "mode": "manual",
            "state": "manual_only",
            "confirmation_required": False,
            "confirmation_prompt": "",
            "operator_hint": "Inspect the persisted run trace before choosing another action.",
        }
    if requires_user_confirmation:
        return {
            "mode": "confirm",
            "state": "requires_confirmation",
            "confirmation_required": True,
            "confirmation_prompt": _confirmation_prompt(option_id),
            "operator_hint": "A surface may offer this as a confirm/cancel action.",
        }
    return {
        "mode": "manual",
        "state": "available",
        "confirmation_required": False,
        "confirmation_prompt": "",
        "operator_hint": "",
    }


def _confirmation_prompt(option_id: str) -> str:
    prompts = {
        "resume_after_user_input": (
            "Resume this MetaSkill run using the collected user input?"
        ),
        "cancel_run": "Cancel this awaiting MetaSkill run?",
        "retry_run": "Retry this MetaSkill run after reviewing the failure?",
        "fallback_to_normal_turn": (
            "Stop using this MetaSkill result and let the parent agent continue?"
        ),
    }
    return prompts.get(option_id, f"Confirm recovery action {option_id!r}?")


def format_report_notice(report: dict[str, Any] | None) -> str:
    """Format a one-line warning for non-passing metacognition reports.

    Clean runs intentionally produce no notice so normal meta_invoke output
    remains stable. Warning and blocked runs expose enough signal for tool
    consumers, logs, and CLI surfaces to know there is something to inspect.
    """

    summary = summarize_report(report)
    if summary is None or summary["status"] == "passed":
        return ""

    counts = summary["signal_counts"]
    count_bits = [
        f"{severity}={counts[severity]}"
        for severity in ("blocked", "warning", "info")
        if counts[severity]
    ]
    counts_text = ", ".join(count_bits) if count_bits else "none"
    first_signal = _first_actionable_signal(report)
    signal_text = f" First signal: {_format_signal_brief(first_signal)}" if first_signal else ""
    return (
        f"Metacognition: {summary['status']} ({counts_text}). "
        f"{summary['summary']}{signal_text}"
    )


def _decision_notice(
    *,
    action: DecisionAction,
    reason: str,
    suggested_next_step: str,
    summary: dict[str, Any],
) -> str:
    if action == "pass":
        return ""
    counts = summary["signal_counts"]
    count_bits = [
        f"{severity}={counts[severity]}"
        for severity in ("blocked", "warning", "info")
        if counts[severity]
    ]
    counts_text = ", ".join(count_bits) if count_bits else "none"
    return (
        f"Metacognitive decision: {action} "
        f"(report={summary['status']}, signals={counts_text}). "
        f"{reason} Suggested next step: {suggested_next_step}"
    )


def _first_actionable_signal(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if not report:
        return None
    signals = [
        signal for signal in report.get("signals", [])
        if isinstance(signal, dict)
    ]
    for severity in ("blocked", "warning", "info"):
        for signal in signals:
            if signal.get("severity") == severity:
                return signal
    return None


def _format_signal_brief(signal: dict[str, Any]) -> str:
    kind = str(signal.get("kind") or "unknown_signal")
    step_id = signal.get("step_id")
    message = str(signal.get("message") or "").strip()
    bits = [kind]
    if step_id:
        bits.append(f"step={step_id}")
    if message:
        bits.append(_clip(message, 180))
    return " | ".join(bits)


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


__all__ = [
    "DecisionAction",
    "RecoveryExecutionMode",
    "RecoveryExecutionState",
    "MetacognitiveController",
    "MetacognitiveDecision",
    "MetacognitiveRecoveryOption",
    "MetacognitiveRecoveryPlan",
    "MetacognitiveSignal",
    "ReportStatus",
    "RecoveryAction",
    "Severity",
    "annotate_recovery_with_result",
    "decide_completion",
    "format_decision_notice",
    "format_recovery_notice",
    "format_recovery_option_brief",
    "format_recovery_result_notice",
    "format_report_notice",
    "plan_recovery",
    "refresh_report_final_text",
    "summarize_report",
]

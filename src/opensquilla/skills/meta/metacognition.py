"""Metacognitive monitoring for MetaSkill runs.

The first implementation is intentionally observational: it models the run,
records reliability signals, and attaches a completion report. It does not
rewrite plans or auto-intervene yet, which keeps the runtime behaviour stable
while giving future control policies a real hook surface.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from opensquilla.skills.meta.types import MetaMatch, MetaPaused, MetaStep

Severity = Literal["info", "warning", "blocked"]
ReportStatus = Literal["passed", "warning", "blocked"]
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
    "MetacognitiveController",
    "MetacognitiveSignal",
    "ReportStatus",
    "Severity",
    "format_report_notice",
    "refresh_report_final_text",
    "summarize_report",
]

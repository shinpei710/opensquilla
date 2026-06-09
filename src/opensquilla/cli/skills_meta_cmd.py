"""`opensquilla skills meta ...` subcommand tree.

Currently exposes ``runs {list, show, steps, failures, replay}``. The
``meta_app`` container is forward-compatible with P0 #2 (which will add
``list``/``show``/``validate`` siblings); this PR ships only ``runs``.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import typer

from opensquilla.paths import state_dir
from opensquilla.persistence.meta_run_writer import (
    MetaRunWriter,
    RunRecord,
    StepRecord,
    open_meta_run_writer,
)
from opensquilla.skills.meta.metacognition import (
    decide_completion,
    format_recovery_option_brief,
    plan_recovery,
    summarize_report,
)
from opensquilla.skills.meta.parser import parse_meta_plan
from opensquilla.skills.meta.types import MetaPlan

meta_app = typer.Typer(
    help="Meta-skill operations: runs, replay, proposals.",
)
runs_app = typer.Typer(help="Meta-skill execution history.")
meta_app.add_typer(runs_app, name="runs")


def _resolve_db_path() -> str:
    """Resolve the meta-skill runs SQLite path used by the gateway writer.

    Resolution order (matches the gateway's ``_state_path(config, ...)``
    helper so the CLI sees the same rows the running gateway writes):

      1. ``OPENSQUILLA_META_RUNS_DB`` env var (explicit override)
      2. ``GatewayConfig.state_dir`` (loaded from
         ``OPENSQUILLA_GATEWAY_CONFIG_PATH`` env var,
         ``./opensquilla.toml``, or ``~/.opensquilla/config.toml`` —
         identical precedence to the gateway's own loader)
      3. ``~/.opensquilla/state/sessions.db`` (built-in default)

    The earlier (1)+(3) shortcut missed any deployment that customised
    ``state_dir`` in toml — operators ran ``opensquilla skills meta runs
    list`` and saw "(no runs)" while the gateway was happily writing
    to a different directory.
    """
    env = os.environ.get("OPENSQUILLA_META_RUNS_DB")
    if env:
        return env

    # Load GatewayConfig to honour state_dir from the same source the
    # gateway uses. Local import to keep the CLI startup path lean.
    try:
        from opensquilla.gateway.config import GatewayConfig

        config_path_env = os.environ.get("OPENSQUILLA_GATEWAY_CONFIG_PATH", "").strip()
        cfg = GatewayConfig.load(config_path_env or None)
        configured = (cfg.state_dir or "").strip()
        if configured:
            return os.path.join(configured, "sessions.db")
    except Exception:  # noqa: BLE001 — fall back to default on any load failure
        pass

    return str(state_dir("sessions.db"))


def _open_writer() -> MetaRunWriter:
    return open_meta_run_writer(_resolve_db_path())


def _parse_since(value: str | None) -> int | None:
    if value is None:
        return None
    now_ms = int(time.time() * 1000)
    unit = value[-1]
    if unit in "hH":
        n = int(value[:-1])
        return now_ms - n * 3600 * 1000
    if unit in "dD":
        n = int(value[:-1])
        return now_ms - n * 86400 * 1000
    if unit in "mM":
        n = int(value[:-1])
        return now_ms - n * 60 * 1000
    raise typer.BadParameter("--since must end in m/h/d (e.g., 5m, 24h, 7d)")


def _serialize_record(rec: RunRecord) -> dict[str, Any]:
    d = asdict(rec)
    d["steps"] = [asdict(s) for s in rec.steps]
    report_raw = d.pop("metacognition_json", None)
    decision_raw = d.pop("metacognition_decision_json", None)
    recovery_raw = d.pop("metacognition_recovery_json", None)
    recovery_result_raw = d.pop("metacognition_recovery_result_json", None)
    report = _parse_metacognition_json(report_raw)
    decision = _decision_from_json_or_report(
        decision_raw,
        report,
    )
    d["metacognition"] = report
    d["metacognition_decision"] = decision
    d["metacognition_recovery"] = _recovery_from_json_or_decision(
        recovery_raw,
        report,
        decision,
    )
    d["metacognition_recovery_result"] = _parse_metacognition_json(
        recovery_result_raw,
    )
    return d


def _serialize_step(step: StepRecord) -> dict[str, Any]:
    return asdict(step)


def _print_runs_table(rows: list[RunRecord]) -> None:
    if not rows:
        typer.echo("(no runs)")
        return
    typer.echo(f"{'RUN_ID':28} {'META_SKILL':30} {'STATUS':10} {'TRIGGER':17} {'STARTED':20}")
    for r in rows:
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.started_at_ms / 1000))
        typer.echo(
            f"{r.run_id:28} {r.meta_skill_name:30.30} {r.status:10} "
            f"{r.triggered_by:17} {started}"
        )


def _parse_metacognition_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "unknown", "summary": "Stored report is not valid JSON."}
    return payload if isinstance(payload, dict) else None


def _decision_from_json_or_report(
    raw: str | None,
    report: dict[str, Any] | None,
) -> dict[str, Any] | None:
    decision = _parse_metacognition_json(raw)
    if decision is not None:
        return decision
    return decide_completion(report)


def _recovery_from_json_or_decision(
    raw: str | None,
    report: dict[str, Any] | None,
    decision: dict[str, Any] | None,
) -> dict[str, Any] | None:
    recovery = _parse_metacognition_json(raw)
    if recovery is not None:
        return recovery
    return plan_recovery(report, decision)


def _recovery_option_by_id(
    recovery: dict[str, Any] | None,
    action: str,
) -> dict[str, Any] | None:
    if not recovery:
        return None
    for option in recovery.get("options", []):
        if isinstance(option, dict) and option.get("id") == action:
            return option
    return None


def _confirmation_prompt_for_action(
    action: str,
    option: dict[str, Any] | None,
) -> str:
    execution = option.get("execution") if isinstance(option, dict) else None
    if isinstance(execution, dict):
        prompt = str(execution.get("confirmation_prompt") or "").strip()
        if prompt:
            return prompt
    if action == "cancel_run":
        return "Cancel this awaiting MetaSkill run?"
    return f"Confirm recovery action {action!r}?"


def _recover_payload(
    *,
    run_id: str,
    action: str,
    status: str,
    reason: str,
    confirmation_prompt: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "action": action,
        "status": status,
        "reason": reason,
    }
    if confirmation_prompt:
        payload["confirmation_prompt"] = confirmation_prompt
    return payload


def _metacognition_counts_text(summary: dict[str, Any]) -> str:
    counts = summary.get("signal_counts", {})
    if not isinstance(counts, dict):
        return ""
    parts = [
        f"{name}={counts.get(name, 0)}"
        for name in ("blocked", "warning", "info")
        if counts.get(name, 0)
    ]
    return ", ".join(parts) if parts else "none"


@runs_app.command("list")
def runs_list(
    name: str | None = typer.Option(None, "--name", help="Filter by meta-skill name"),
    status: str | None = typer.Option(None, "--status", help="ok|failed|running|cancelled"),
    session: str | None = typer.Option(None, "--session", help="Filter by session_key"),
    since: str | None = typer.Option(None, "--since", help="e.g., 5m, 24h, 7d"),
    limit: int = typer.Option(50, "--limit"),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """List meta-skill runs."""
    writer = _open_writer()
    try:
        rows = writer.list_runs(
            name=name,
            status=status,
            session_key=session,
            since_ms=_parse_since(since),
            limit=limit,
        )
    finally:
        writer.close()

    if json_out:
        typer.echo(json.dumps([_serialize_record(r) for r in rows], default=str))
    else:
        _print_runs_table(rows)


@runs_app.command("show")
def runs_show(
    run_id: str = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show a single run with its steps."""
    writer = _open_writer()
    try:
        rec = writer.get_run(run_id)
    finally:
        writer.close()
    if rec is None:
        typer.echo(f"run not found: {run_id}", err=True)
        raise typer.Exit(2)

    if json_out:
        typer.echo(json.dumps(_serialize_record(rec), default=str))
        return

    typer.echo(f"run_id:        {rec.run_id}")
    typer.echo(f"meta_skill:    {rec.meta_skill_name}")
    typer.echo(f"digest:        {rec.meta_skill_digest[:16]}...")
    typer.echo(f"status:        {rec.status}")
    typer.echo(f"triggered_by:  {rec.triggered_by}")
    typer.echo(f"session_key:   {rec.session_key}")
    typer.echo(
        "started:       "
        + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec.started_at_ms / 1000))
    )
    if rec.ended_at_ms:
        typer.echo(
            "ended:         "
            + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec.ended_at_ms / 1000))
        )
    if rec.final_text:
        typer.echo(f"final_text:    {rec.final_text[:200]}...")
    if rec.error:
        typer.echo(f"error:         {rec.error}")
    report = _parse_metacognition_json(rec.metacognition_json)
    decision = _decision_from_json_or_report(
        rec.metacognition_decision_json,
        report,
    )
    recovery = _recovery_from_json_or_decision(
        rec.metacognition_recovery_json,
        report,
        decision,
    )
    recovery_result = _parse_metacognition_json(
        rec.metacognition_recovery_result_json,
    )
    report_summary = summarize_report(report)
    if report_summary is not None:
        typer.echo(
            "metacognition: "
            f"{report_summary['status']} "
            f"({_metacognition_counts_text(report_summary)})"
        )
        if report_summary["summary"]:
            typer.echo(f"meta_summary:  {report_summary['summary']}")
    if decision is not None:
        typer.echo(f"meta_decision: {decision.get('action', 'unknown')}")
        reason = str(decision.get("reason") or "").strip()
        if reason:
            typer.echo(f"decision_reason: {reason}")
        next_step = str(decision.get("suggested_next_step") or "").strip()
        if next_step:
            typer.echo(f"decision_next: {next_step}")
    if recovery is not None:
        typer.echo(
            "recovery:      "
            f"{recovery.get('primary_action', 'none')}"
        )
        options = [
            format_recovery_option_brief(option)
            for option in recovery.get("options", [])
            if isinstance(option, dict) and option.get("id")
        ]
        if options:
            typer.echo(f"recovery_opts: {', '.join(options)}")
    if recovery_result is not None:
        typer.echo(
            "recovery_result: "
            f"{recovery_result.get('status', 'unknown')}"
        )
        reason = str(recovery_result.get("reason") or "").strip()
        if reason:
            typer.echo(f"recovery_detail: {reason}")
    typer.echo(f"steps:         {len(rec.steps)}")


@runs_app.command("steps")
def runs_steps(
    run_id: str = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show step-by-step trace of a run."""
    writer = _open_writer()
    try:
        steps = writer.get_steps(run_id)
    finally:
        writer.close()
    if not steps:
        typer.echo(f"no steps for run: {run_id}", err=True)
        raise typer.Exit(2)

    if json_out:
        typer.echo(json.dumps([_serialize_step(s) for s in steps], default=str))
        return

    typer.echo(f"{'STEP':12} {'KIND':14} {'SKILL':24} {'STATUS':12} {'DURATION':12}")
    for s in steps:
        dur = (
            f"{s.ended_at_ms - s.started_at_ms}ms"
            if s.ended_at_ms is not None
            else "—"
        )
        typer.echo(
            f"{s.step_id:12} {s.step_kind:14} {s.effective_skill:24.24} "
            f"{s.status:12} {dur:12}"
        )


@runs_app.command("failures")
def runs_failures(
    name: str | None = typer.Option(None, "--name"),
    since: str | None = typer.Option(None, "--since"),
    limit: int = typer.Option(50, "--limit"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List failed runs."""
    writer = _open_writer()
    try:
        rows = writer.list_failures(
            name=name, since_ms=_parse_since(since), limit=limit,
        )
    finally:
        writer.close()
    if json_out:
        typer.echo(json.dumps([_serialize_record(r) for r in rows], default=str))
    else:
        _print_runs_table(rows)


def _deserialize_plan(snapshot_json: str) -> MetaPlan:
    """Restore a MetaPlan snapshot from its JSON column.

    Delegates to ``plan_serde.from_jsonable`` so we honour the envelope
    format (``{"v": 1, "plan": {...}}``) PR2 introduced and still accept
    legacy snapshots written before PR2 (which used the bare plan dict).
    """
    from opensquilla.skills.meta.plan_serde import from_jsonable
    return from_jsonable(json.loads(snapshot_json))


def _print_dag(
    plan: MetaPlan,
    plan_source: str,
    rendered_inputs_by_step: dict[str, dict[str, Any]],
) -> None:
    typer.echo(f"Meta-skill: {plan.name}     Source: {plan_source}")
    typer.echo(f"Trigger priority: {plan.priority}")
    typer.echo("DAG (topological order):")
    for i, step in enumerate(plan.steps, 1):
        typer.echo(f"  [{i:02}] {step.id}  kind={step.kind}  skill={step.skill}")
        if step.depends_on:
            typer.echo(f"       depends_on: {list(step.depends_on)}")
        if step.on_failure:
            typer.echo(f"       on_failure: {step.on_failure}")
        rendered = rendered_inputs_by_step.get(step.id, {})
        if rendered:
            typer.echo("       rendered inputs (truncated to 200 chars per field):")
            for k, v in rendered.items():
                s = str(v)[:200]
                typer.echo(f"         {k}: {s}")


def _dag_to_json(
    plan: MetaPlan,
    plan_source: str,
    run_id: str,
    rendered_inputs_by_step: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "meta_skill_name": plan.name,
        "plan_source": plan_source,
        "trigger_priority": plan.priority,
        "steps": [
            {
                "order": i,
                "step_id": s.id,
                "kind": s.kind,
                "declared_skill": s.skill,
                "effective_skill": s.skill,
                "depends_on": list(s.depends_on),
                "on_failure": s.on_failure or None,
                "rendered_inputs": rendered_inputs_by_step.get(s.id, {}),
            }
            for i, s in enumerate(plan.steps, 1)
        ],
    }


@runs_app.command("replay")
def runs_replay(
    run_id: str = typer.Argument(...),
    latest: bool = typer.Option(
        False, "--latest", help="Use current registered plan, not historical snapshot",
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Replay a historical meta-skill run."""
    writer = _open_writer()
    try:
        rec = writer.get_run(run_id)
    finally:
        writer.close()
    if rec is None:
        typer.echo(f"run not found: {run_id}", err=True)
        raise typer.Exit(2)

    if latest:
        from opensquilla.skills.loader import SkillLoader

        loader = SkillLoader()
        spec = loader.get_by_name(rec.meta_skill_name)
        if spec is None:
            typer.echo(
                "meta-skill no longer registered; cannot --latest replay", err=True,
            )
            raise typer.Exit(2)
        plan = parse_meta_plan(spec)
        if plan is None:
            typer.echo("meta-skill spec exists but is not a meta-skill", err=True)
            raise typer.Exit(2)
        plan_source = "latest_registered"
    else:
        plan = _deserialize_plan(rec.plan_snapshot_json)
        plan_source = "historical_snapshot"

    rendered_by_step: dict[str, dict[str, Any]] = {}
    for s in rec.steps:
        try:
            rendered_by_step[s.step_id] = json.loads(s.rendered_inputs_json)
        except json.JSONDecodeError:
            pass

    if dry_run:
        if json_out:
            typer.echo(
                json.dumps(
                    _dag_to_json(plan, plan_source, run_id, rendered_by_step),
                    default=str,
                )
            )
        else:
            _print_dag(plan, plan_source, rendered_by_step)
        return

    typer.echo(
        "Live replay requires a running gateway; CLI-direct mode unavailable "
        "in this build.",
        err=True,
    )
    typer.echo("Use --dry-run to inspect the DAG.", err=True)
    raise typer.Exit(2)


@runs_app.command("recover")
def runs_recover(
    run_id: str = typer.Argument(...),
    action: str = typer.Option(..., "--action", help="Recovery option id to execute"),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Actually execute confirmation-gated recovery actions",
    ),
    reason: str = typer.Option(
        "cli_recover_cancel_run",
        "--reason",
        help="Audit reason for supported recovery actions",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Execute a supported confirmed recovery action for one run.

    V7 intentionally supports only ``cancel_run`` for awaiting MetaSkill runs.
    Other recovery options remain surfaced as contracts until their runtime
    execution paths are implemented.
    """
    writer = _open_writer()
    try:
        rec = writer.get_run(run_id)
        if rec is None:
            payload = _recover_payload(
                run_id=run_id,
                action=action,
                status="not_found",
                reason="Run not found.",
            )
            if json_out:
                typer.echo(json.dumps(payload, default=str))
            else:
                typer.echo(f"run not found: {run_id}", err=True)
            raise typer.Exit(2)

        report = _parse_metacognition_json(rec.metacognition_json)
        decision = _decision_from_json_or_report(
            rec.metacognition_decision_json,
            report,
        )
        recovery = _recovery_from_json_or_decision(
            rec.metacognition_recovery_json,
            report,
            decision,
        )
        option = _recovery_option_by_id(recovery, action)
        if option is None and rec.status == "awaiting_user" and action == "cancel_run":
            option = {
                "id": "cancel_run",
                "execution": {
                    "mode": "confirm",
                    "state": "requires_confirmation",
                    "confirmation_required": True,
                    "confirmation_prompt": "Cancel this awaiting MetaSkill run?",
                },
            }
        prompt = _confirmation_prompt_for_action(action, option)

        if action != "cancel_run":
            payload = _recover_payload(
                run_id=run_id,
                action=action,
                status="unsupported",
                reason=(
                    "This recovery action is not executable by CLI yet; "
                    "inspect the run or use a live gateway/runtime surface."
                ),
                confirmation_prompt=prompt,
            )
            if json_out:
                typer.echo(json.dumps(payload, default=str))
            else:
                typer.echo(payload["reason"], err=True)
            raise typer.Exit(2)

        if rec.status != "awaiting_user":
            payload = _recover_payload(
                run_id=run_id,
                action=action,
                status="not_applicable",
                reason="cancel_run only applies to runs in awaiting_user status.",
                confirmation_prompt=prompt,
            )
            if json_out:
                typer.echo(json.dumps(payload, default=str))
            else:
                typer.echo(payload["reason"], err=True)
            raise typer.Exit(2)

        if not confirm:
            payload = _recover_payload(
                run_id=run_id,
                action=action,
                status="requires_confirmation",
                reason="Re-run with --confirm to cancel the awaiting MetaSkill run.",
                confirmation_prompt=prompt,
            )
            if json_out:
                typer.echo(json.dumps(payload, default=str))
            else:
                typer.echo(prompt)
                typer.echo(
                    "Re-run with --confirm to cancel the awaiting MetaSkill run.",
                )
            raise typer.Exit(2)

        cancelled = writer.mark_cancelled(run_id=run_id, reason=reason)
        if not cancelled:
            payload = _recover_payload(
                run_id=run_id,
                action=action,
                status="not_applicable",
                reason="Run was no longer awaiting user input.",
            )
            if json_out:
                typer.echo(json.dumps(payload, default=str))
            else:
                typer.echo(payload["reason"], err=True)
            raise typer.Exit(2)
    finally:
        writer.close()

    payload = _recover_payload(
        run_id=run_id,
        action=action,
        status="cancelled",
        reason=reason,
    )
    if json_out:
        typer.echo(json.dumps(payload, default=str))
        return
    typer.echo(f"cancelled awaiting MetaSkill run: {run_id}")


# ─── Proposals: list / accept ─────────────────────────────────────────────
# meta-skill-creator's `persist` step writes candidate SKILL.md files to
# ~/.opensquilla/proposals/<id>/ alongside a gates.json (lint/smoke
# results). Acceptance promotes a proposal into ~/.opensquilla/skills/
# so the next gateway boot picks it up as a MANAGED-layer skill. The
# core logic mirrors the in-tree
# ``skills/bundled/skill-creator-proposals/scripts/proposals.py`` cmd_accept
# so the CLI and the in-meta-skill code path stay byte-identical.


def _proposals_home() -> Path:
    from opensquilla.paths import default_opensquilla_home

    return Path(default_opensquilla_home())


def _proposals_dir() -> Path:
    return _proposals_home() / "proposals"


def _skills_managed_dir() -> Path:
    return _proposals_home() / "skills"


@meta_app.command("proposals")
def proposals_cmd(
    action: str = typer.Argument(
        ..., help="list | accept | show — proposal CRUD action",
    ),
    proposal_id: str | None = typer.Argument(
        None,
        help="8-hex proposal id (required for accept/show)",
    ),
    force: bool = typer.Option(
        False, "--force", help="Accept even when gates did not all pass",
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """List, inspect, or accept meta-skill proposals.

    ``proposals list``                  — enumerate all candidates
    ``proposals show <id>``             — print one candidate's SKILL.md + gates
    ``proposals accept <id> [--force]`` — promote to MANAGED-layer skill
    """
    import json as _json
    import re
    import shutil

    proposals_dir = _proposals_dir()

    if action == "list":
        rows: list[dict[str, Any]] = []
        if proposals_dir.is_dir():
            for sub in sorted(proposals_dir.iterdir()):
                if not sub.is_dir():
                    continue
                gates_path = sub / "gates.json"
                gates: dict[str, Any] = {}
                if gates_path.is_file():
                    try:
                        gates = _json.loads(gates_path.read_text())
                    except _json.JSONDecodeError:
                        gates = {}
                rows.append({
                    "proposal_id": sub.name,
                    "auto_enable_eligible": bool(
                        gates.get("auto_enable_eligible", False),
                    ),
                    "skill_md_present": (sub / "SKILL.md").is_file(),
                })
        if json_out:
            typer.echo(_json.dumps({"proposals": rows}, indent=2))
            return
        if not rows:
            typer.echo("(no proposals)")
            return
        typer.echo(f"{'PROPOSAL_ID':12} ELIGIBLE  SKILL_MD")
        typer.echo("-" * 40)
        for r in rows:
            typer.echo(
                f"{r['proposal_id']:12} "
                f"{('yes' if r['auto_enable_eligible'] else 'no'):8}  "
                f"{'present' if r['skill_md_present'] else 'MISSING'}"
            )
        return

    if action in ("show", "accept") and not proposal_id:
        typer.echo(f"Error: '{action}' requires a proposal_id argument", err=True)
        raise typer.Exit(2)

    # ID format check defends against path-traversal — mirrors the script's
    # I1 hardening (uuid.uuid4().hex[:8] write side, 8 hex on read side).
    if proposal_id and not re.fullmatch(r"[0-9a-f]{8}", proposal_id):
        typer.echo(
            f"Error: invalid proposal_id {proposal_id!r} "
            "(expected 8 lowercase hex chars)",
            err=True,
        )
        raise typer.Exit(2)

    src = proposals_dir / (proposal_id or "")

    if action == "show":
        if not (src / "SKILL.md").is_file():
            typer.echo(f"Error: proposal {proposal_id} not found", err=True)
            raise typer.Exit(1)
        gates_text = ""
        if (src / "gates.json").is_file():
            gates_text = (src / "gates.json").read_text()
        skill_md = (src / "SKILL.md").read_text(encoding="utf-8")
        if json_out:
            typer.echo(_json.dumps({
                "proposal_id": proposal_id,
                "skill_md": skill_md,
                "gates": _json.loads(gates_text) if gates_text else {},
            }, indent=2))
            return
        typer.echo(f"=== Proposal {proposal_id} ===")
        if gates_text:
            typer.echo("\n-- gates.json --")
            typer.echo(gates_text)
        typer.echo("\n-- SKILL.md --")
        typer.echo(skill_md)
        return

    # action == "accept"
    if not (src / "SKILL.md").is_file():
        typer.echo(f"Error: proposal {proposal_id} not found", err=True)
        raise typer.Exit(1)

    gates = {}
    if (src / "gates.json").is_file():
        try:
            gates = _json.loads((src / "gates.json").read_text())
        except _json.JSONDecodeError:
            gates = {}
    if not gates.get("auto_enable_eligible") and not force:
        typer.echo(
            f"Refused: gates did not all pass for {proposal_id}. "
            "Use --force to override.",
            err=True,
        )
        if gates:
            typer.echo(_json.dumps(gates, indent=2), err=True)
        raise typer.Exit(1)

    skill_md = (src / "SKILL.md").read_text(encoding="utf-8")
    # Accept both quoted and unquoted YAML names (N3 fix).
    name_match = re.search(r'^name:\s*"?([\w\-]+)"?\s*$', skill_md, re.MULTILINE)
    if not name_match:
        typer.echo(
            "Error: cannot parse skill name from SKILL.md frontmatter",
            err=True,
        )
        raise typer.Exit(1)
    name = name_match.group(1)

    dst = _skills_managed_dir() / name
    if dst.exists():
        typer.echo(
            f"Refused: skill {name!r} already exists at {dst}. "
            "Remove the existing copy first or rename the proposal.",
            err=True,
        )
        raise typer.Exit(1)

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    typer.echo(
        f"✅ Accepted proposal {proposal_id} as skill `{name}` at {dst}\n"
        "Restart the gateway to load the new skill from the MANAGED layer."
    )
    if json_out:
        typer.echo(_json.dumps({
            "status": "ok",
            "proposal_id": proposal_id,
            "name": name,
            "skill_path": str(dst),
        }))

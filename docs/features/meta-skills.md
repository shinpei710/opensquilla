# Meta-Skills

Meta-skills package repeatable multi-step work as reusable, inspectable
workflows. Use them when a request needs more than one normal skill, tool,
checkpoint, or final synthesis pass.

For the full user-facing guide, read
[`meta-skill-user-guide.md`](meta-skill-user-guide.md). For authoring rules,
read [`../authoring/meta-skills.md`](../authoring/meta-skills.md).

## Skills vs Meta-Skills

| Capability | Use it for |
| --- | --- |
| Skill | One focused task pattern, instruction set, script, or tool helper. |
| Meta-skill | A reusable workflow made of multiple steps, skills, checks, or outputs. |

For example, "summarize this document" is skill-shaped. "Turn this contract,
quote, and email into a sign, reject, or negotiate recommendation with risks and
next actions" is meta-skill-shaped.

## Stable Built-In MetaSkills

The retained stable catalog is intentionally small:

| MetaSkill | Positioning |
| --- | --- |
| `meta-kid-project-planner` | Produces safe, age-appropriate plans for school projects, show-and-tell, or science activities. |
| `meta-paper-write` | Supports academic drafts, manuscript structure, citation planning, experiment placeholders, and LaTeX/PDF paths. |
| `meta-short-drama` | Produces short-drama scripts, visual prompts, subtitles, and local video artifacts. |
| `meta-skill-creator` | Turns repeated multi-skill collaboration patterns into new MetaSkill proposals. |

Experimental meta-skills may exist under development trees, but this page lists
only bundled built-ins that should be presented as retained product
capabilities.

## Requirements

Use the Skill page detail dialog before running a MetaSkill. Its
**Requirements** section shows the MetaSkill's own requirements plus one-hop
requirements from child skills.

- `meta-paper-write` needs `xelatex` and `bibtex` for PDF compilation.
- `meta-short-drama` needs `ffmpeg` and `ffprobe` for local video rendering,
  merge, and subtitle steps.
- Document/report MetaSkills inherit readiness from child skills such as
  `docx`, `xlsx`, `pdf-toolkit`, `pptx`, `multi-search-engine`, and `weather`.

## How to Ask

Ask for the outcome and the standard:

```text
Create a decision memo comparing travel eSIM, carrier roaming, and local SIM
options for my parents' 8-day Japan trip. Include sources, risks, a final
recommendation, and what I should order tonight.
```

For important or easily confused work, name the workflow:

```text
Use meta-skill `meta-paper-write`.

Draft a workshop paper about local-first agent orchestration with citation
planning, a clear experiment placeholder section, and a LaTeX-ready outline.
```

A strong request usually includes:

- outcome;
- context;
- decision standard;
- expected output;
- constraints;
- actions the agent must not take.

## Discover Meta-Skills

List and search skills:

```sh
opensquilla skills list
opensquilla skills search meta
```

Inspect a meta-skill composition:

```sh
opensquilla skills inspect <meta-skill-name>
```

The inspect command shows the compiled step shape before you rely on a workflow.

## Inspect Run History

List recent runs:

```sh
opensquilla skills meta runs list
```

Inspect one run:

```sh
opensquilla skills meta runs show <run-id>
opensquilla skills meta runs steps <run-id>
opensquilla skills meta runs failures --since 24h
```

Preview replay shape without executing live work:

```sh
opensquilla skills meta runs replay <run-id> --dry-run
```

## Metacognitive Monitoring

MetaSkill runs now attach an observational metacognition report to the terminal
`MetaResult`. The report is a lightweight reliability snapshot, not an
auto-rewrite policy:

- run state: total, started, finished, skipped, failed, paused, and failover
  counts;
- completion evidence: whether the run completed, paused, produced final text,
  and captured step outputs;
- reliability signals: empty outputs, hard failures, failovers, missing inputs,
  and pause events.

This first layer deliberately avoids changing the workflow plan while it runs.
It gives replay, diagnostics, and future control policies a stable state model
before OpenSquilla attempts stronger interventions such as pausing a run,
switching a MetaSkill, or requiring additional verification.

The report is also surfaced through the normal execution tools:

- clean `passed` reports stay quiet so successful `meta_invoke` output is not
  polluted;
- non-passing decisions add a compact `Metacognitive decision:` notice to the
  terminal `meta_invoke` tool result;
- completed reports are stored on `meta_skill_runs.metacognition_json` and are
  visible with `opensquilla skills meta runs show <run-id>` or its `--json`
  output.

### Metacognitive Completion Gate

The report now feeds a conservative completion-gate decision:

- `pass`: no completion issues were detected;
- `warn`: a deliverable exists, but warning signals should remain visible;
- `block`: the run should not be treated as a normal completed answer;
- `needs_review`: the run paused or requires user/operator attention before
  completion.

This policy still does not rewrite the DAG or auto-select a different
MetaSkill. It creates a stable decision boundary first: `meta_invoke` can avoid
presenting blocked results as ordinary success, while `skills meta runs show`
and `--json` expose the stored `metacognition_decision`.

### Controlled Recovery

Completion-gate decisions also produce a `metacognition_recovery` plan with a
machine-readable `primary_action` and ordered `options`. Examples include
`deliver_with_warning`, `regenerate_final_text`, `collect_user_input`,
`retry_or_fallback`, and `inspect_run`.

Most recovery plans are advisory. They are persisted on
`meta_skill_runs.metacognition_recovery_json` and surfaced in `meta_invoke`
tool results so operators can see the next safe action.

Each recovery option now carries an `execution` contract that tells surfaces
how to treat it:

- `mode=automatic`: the orchestrator may run the action inside a bounded
  policy;
- `mode=confirm`: a surface may offer a confirm/cancel action, but runtime must
  not execute it without user confirmation;
- `mode=manual`: the operator should inspect or perform the action manually;
- `mode=surface`: the surface can deliver or display the result without a new
  runtime action.

OpenSquilla can now execute one bounded recovery action automatically:
`regenerate_final_text`. It only runs when the completion gate blocked a
successful MetaSkill run because no user-facing final text was produced, the
recovery plan names `regenerate_final_text` as its `primary_action`, an
`llm_chat` dependency is available, and at least one captured step output is
non-empty. The orchestrator synthesizes a final answer from those existing
step outputs, refreshes the metacognition report, and records the execution
result on `meta_skill_runs.metacognition_recovery_result_json`.

This recovery does not rerun the DAG, switch MetaSkills, retry failed steps, or
execute arbitrary recovery options. Skipped and failed attempts are written
back into the matching option's `execution.last_status`, so `meta_invoke` and
`skills meta runs show` can explain why no recovery was applied.

### CLI Recovery Execution

The first confirmation-gated CLI recovery action is available for awaiting
runs:

```sh
opensquilla skills meta runs recover <run-id> --action cancel_run --confirm
```

Without `--confirm`, the command prints the confirmation prompt and leaves the
run unchanged. In this release, `cancel_run` is the only CLI-executable
recovery action. Higher-impact actions such as `retry_run` and
`fallback_to_normal_turn` remain visible in the recovery contract but return an
explicit unsupported status until their gateway/runtime execution paths are
implemented.

The CLI can also validate and prepare `resume_after_user_input` payloads for an
awaiting run:

```sh
opensquilla skills meta runs recover <run-id> \
  --action resume_after_user_input \
  --fields-json '{"destination":"Tokyo","days":5}' \
  --json
```

Without `--confirm`, this behaves as a dry run: it returns the awaiting schema,
required fields, submitted fields, filled fields, missing fields, and the
confirmation prompt while leaving the run unchanged. With `--confirm`, the
payload status becomes `prepared` when validation succeeds, but the run remains
`awaiting_user`; the actual resume still requires a live gateway/runtime
surface to claim the row and continue the DAG.

To hand the validated payload to a running gateway from the CLI, add
`--gateway`:

```sh
opensquilla skills meta runs recover <run-id> \
  --action resume_after_user_input \
  --fields-json '{"destination":"Tokyo","days":5}' \
  --confirm \
  --gateway
```

This calls `chat.clarify_submit` on the configured gateway. The CLI still does
not claim the row directly; the gateway accepts the submitted fields as a
normal session turn, then the existing runtime path performs the
`awaiting_user -> running` compare-and-swap and streams the resumed DAG.

Gateway surfaces use the same validation contract for structured form
submissions. When WebChat calls `chat.clarify_submit`, the gateway checks the
submitted fields against the persisted awaiting schema and rejects run-id
mismatches or invalid fields before accepting a runtime turn. Valid submissions
still flow through the normal session runtime so the existing
`awaiting_user -> running` compare-and-swap and streaming resume path remain
the single execution path.

## Proposals

Meta-skill creation workflows may write proposals before they become managed
skills. Inspect proposals:

```sh
opensquilla skills meta proposals list
opensquilla skills meta proposals show <proposal-id>
```

Accept a proposal only after review:

```sh
opensquilla skills meta proposals accept <proposal-id>
```

## Safety Model

MetaSkill outputs are reviewable work products and decision-support drafts. They
are not final professional advice in legal, medical, financial, hiring,
academic, security, or other high-stakes contexts.

Actions such as publishing, applying, installing, paying, signing, messaging, or
modifying production systems require explicit user authorization.

---

[Docs index](../README.md) · [Product guide](../../README.product.md) · [Improve this page](../contributing-docs.md) · [Report a docs issue](https://github.com/opensquilla/opensquilla/issues/new?template=docs_report.yml)

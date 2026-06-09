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
| `meta-competitive-intel` | Turns account or competitor signals into sales, BD, or competitive-intel briefs. |
| `meta-daily-operator-brief` | Turns today's tasks, context, and constraints into an operating brief. |
| `meta-document-to-decision` | Turns contracts, quotes, renewals, notices, or spreadsheets into sign, reject, or negotiate decisions. |
| `meta-job-search-pipeline` | Turns a JD, resume, and application goal into an application package and interview prep. |
| `meta-kid-project-planner` | Produces safe, age-appropriate plans for school projects, show-and-tell, or science activities. |
| `meta-paper-write` | Supports academic drafts, manuscript structure, citation planning, experiment placeholders, and LaTeX/PDF paths. |
| `meta-short-drama` | Produces short-drama scripts, visual prompts, subtitles, and local video artifacts. |
| `meta-skill-creator` | Turns repeated multi-skill collaboration patterns into new MetaSkill proposals. |
| `meta-web-research-to-report` | Turns source-backed research needs into reports, briefs, or decision memos. |

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
Use meta-skill `meta-web-research-to-report`.

Create a source-backed decision memo comparing travel eSIM, carrier roaming,
and local SIM options for my parents' 8-day Japan trip.
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

OpenSquilla can now execute one bounded recovery action automatically:
`regenerate_final_text`. It only runs when the completion gate blocked a
successful MetaSkill run because no user-facing final text was produced, the
recovery plan names `regenerate_final_text` as its `primary_action`, an
`llm_chat` dependency is available, and at least one captured step output is
non-empty. The orchestrator synthesizes a final answer from those existing
step outputs, refreshes the metacognition report, and records the execution
result on `meta_skill_runs.metacognition_recovery_result_json`.

This recovery does not rerun the DAG, switch MetaSkills, retry failed steps, or
execute arbitrary recovery options. Skipped and failed attempts are recorded so
`meta_invoke` and `skills meta runs show` can explain why no recovery was
applied.

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

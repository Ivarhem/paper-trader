# Paper Trader Orchestra Task State Contract

Long-running code, research, and review work must leave a compact lifecycle
record so the main conversation can stay responsive.

## State files

- Durable state: `state/agent_tasks.json`
- Compact handoff: `/tmp/agent_task_state_latest.json`
- Delegation packet: `/tmp/local_llm_delegation_latest.json`
- Source-edit batch pause: `state/batch_pause_guard.json`
- Compact source-edit pause: `/tmp/paper_trader_batch_pause_latest.json`

## Required task fields

- `task_id`
- `owner`
- `status`
- `scope`
- `files`
- `checks`
- `attempt_count`
- `summary`
- `updated_at`

Delegated review tasks should also carry `lease_expires_at`,
`verification_gate`, `result_artifact`, and `escalation_reason`.

## Closure rule

A task is not complete until its named verification gate has run or the result
explains why no safe gate applies. Three repeated failures should stop the loop
and trigger reassignment with a smaller brief.

## Safety boundary

This contract does not grant authority to place trades, call broker endpoints,
delete data, relax promotion gates, or restart services. Those remain explicit
Main Ray/human decision points.

## Source-edit pause rule

Before editing source/config that can change batch outputs, run:

```bash
scripts/batch_pause_guard.py enter --owner <agent-or-person> --reason "source edit" --stop-running
```

Cron and background wrappers must call `scripts/batch_pause_guard.py check`
before writing recommendation, validation, issue-context, market-data, or
external-mover artifacts. A paused check exits with code 75, writes a
`skipped_source_edit_pause` compact status, and records a skipped task-state
event without incrementing failure counters. Release the pause only after the
edit has been verified:

```bash
scripts/batch_pause_guard.py leave --owner <agent-or-person> --reason "verified"
```

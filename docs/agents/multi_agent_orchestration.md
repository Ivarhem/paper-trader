# Multi-Agent Orchestration Policy

This repo uses agentic workflows, but the main chat session should stay the
orchestrator/reviewer, not the long-running worker.

## Default roles

- Main Ray: user conversation, task decomposition, risk decisions, final review.
- Worker agent/session: bounded code, config, research, or documentation change.
- Reviewer pass: diff, tests, contract checks, and regression risk.
- Cron/isolated session: recurring or long-running pipeline validation.

## Delegation threshold

Delegate by default when work may take more than about two minutes, may need
retries, or may block on slow historical/pipeline checks. Code/script fixes that
need more than two minutes of validation, plus heavy historical/paper validation,
must be assigned to worker agents/subtasks instead of held in the main Telegram
session. Keep tiny inspection or single-line fixes in the main session when that
is faster and safer.

## Worker brief contract

Every worker task should include:

- task id and owner,
- exact file or module ownership,
- files/modules the worker must not touch,
- acceptance criteria,
- verification commands,
- completion report format: changed files, checks run, residual risk.

Workers should not restart services, place orders, delete data, or send external
messages unless Main Ray explicitly approves that specific action.

## Shared task state

Use `scripts/agent_task_state.py` for durable handoff state:

```bash
scripts/agent_task_state.py start \
  --task-id paper-trader-research-pipeline \
  --owner cron:research_pipeline \
  --kind cron \
  --scope "hourly research pipeline" \
  --files "scripts/run_research_org_cron.sh,tools/agents/research_pipeline_agent.py" \
  --checks "research_pipeline_status,contract_check"
```

State is written to:

- `state/agent_tasks.json` for durable repo-local state,
- `/tmp/agent_task_state_latest.json` for compact monitor/review handoff.

Status values are `in_progress`, `completed`, `failed`, `skipped`, and `blocked`
when a human/approval dependency exists. For delegated cron work, the launcher
uses `owner=cron:*` and the background worker/subtask uses `owner=worker:*` so
monitors show who currently owns the long validation.

## Quality gates

- Plan before code for structural changes.
- One owner per file during parallel work.
- Same failure three times means stop and reassign with a smaller brief.
- Worker completion requires evidence, not just a claim: test, typecheck, smoke,
  contract check, or a precise explanation of why no gate applies.
- Main Ray reviews final diff and verification evidence before treating the task
  as done.

## Paper-trading boundary

All research agents remain paper/historical only. No broker calls, real orders,
live position management, or production policy relaxation is allowed from a
worker task.

## Memory and learning

Workers may leave reflection notes or task-state summaries. Long-lived operating
rules in `AGENTS.md` or skill files require Main Ray/human review instead of
blind automatic edits.

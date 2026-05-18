# Paper Trader Research Agents

This directory is a lightweight multi-agent research organization.

It is **not** a CrewAI/LangGraph-style chat-agent setup. Each agent is a
reproducible Python micro-agent with:

- one operational role,
- explicit inputs/outputs,
- JSON contract metadata,
- no real trading authority,
- shared state through `paper_trader.db` and `/tmp/*_latest.json`.

The orchestrator is `research_pipeline_agent.py`, normally run by server cron.
See `configs/research_agents.yaml` for the organization manifest and role map.

## Context-efficient delegation

The pipeline writes compact CODEX-goal-style context artifacts before any large
debug payload is needed:

- `/tmp/research_pipeline_status.json`
- `/tmp/context_goal_latest.json`
- `/tmp/recommendations_status_latest.json`
- `/tmp/audit_status_latest.json`
- `/tmp/local_llm_delegation_latest.json`

Use `/tmp/local_llm_delegation_latest.json` as the handoff packet for local LLM
or subagent review work. Each task is bounded, paper-only, and points to compact
inputs. Full artifacts are for targeted debugging after the compact task shows a
real anomaly or code-level follow-up.

## Safety boundary

Historical/paper research only:

- no broker API calls,
- no real orders,
- no live position management,
- recommendations are research/watch artifacts only.

## Agent contract expectation

Agents should use `tools/agents/lib/agent_contract.py::attach_contract` and write
JSON with at least:

- `run_at`
- `mode`
- `real_trading: false`
- `summary` or metrics
- `contract.status`
- `contract.metrics`
- `contract.warnings`
- `contract.next_actions`

## Design rule

If an agent produces the same caution for nearly every candidate, it should
promote that to aggregate/org-level guidance instead of noisy per-card blocking.

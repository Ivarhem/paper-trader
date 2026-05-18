# AGENTS.md - Paper Trader Guide

## Scope
- Historical and paper-trading research only. Do not place real orders, call broker endpoints, or add exchange credentials.
- Production repo root is `/service/services/paper_trader`; service path is `/paper-trader`.
- Prefer small, verifiable changes. There may be local uncommitted work, so inspect before editing and do not revert unrelated changes.

## Read First
- App entry points: `app/main.py`, `app/database.py`, `app/schemas.py`, `app/symbols.py`.
- Research pipeline entry point: `tools/agents/research_pipeline_agent.py`.
- Agent role map/config: `configs/research_agents.yaml`, `configs/research_pipeline.yaml`, `tools/agents/README.md`.
- UI files: `static/index.html`, `static/monitor.html`, `static/app.js`, `static/monitor.js`, `static/style.css`.

## Generated Outputs
- Large runtime artifacts are generated under `/tmp/*_latest.json`, `state/latest/*`, and `static/*_latest.json`.
- Default to CODEX-goal-style compact context: read `/tmp/research_pipeline_status.json`, `/tmp/context_goal_latest.json`, `/tmp/recommendations_status_latest.json`, `/tmp/audit_status_latest.json`, and `/tmp/local_llm_delegation_latest.json` first.
- Durable run metadata is indexed in the `agent_runs` table; use it for status/history lookups before opening large artifact JSON.
- Do not read large generated JSON/static outputs before code changes unless the task specifically requires full runtime details.
- Use full artifacts only by targeted need: `/tmp/research_pipeline_latest.json`, `/tmp/recommendations_latest.json`, `/tmp/recommendation_audit_latest.json`, and especially `/tmp/recommendation_audit_full_latest.json` are detail/debug sources, not first-read context.
- API defaults should stay compact; request `detail=full` only for focused diagnosis.
- For local LLM/subagent delegation, pass one task from `/tmp/local_llm_delegation_latest.json` plus the listed compact inputs. Escalate to Codex only when code edits, full-artifact diagnosis, or production behavior changes are required.

## Agent Roles
- `research_pipeline_agent.py` orchestrates scheduled research agents and writes summary artifacts.
- Strategy validation/promotion lives around `strategy_generator_agent.py`, `simulation_validation_worker.py`, `strategy_novelty_pruner.py`, `strategy_lifecycle_agent.py`, and `active_strategy_balancer_agent.py`.
- Recommendation assembly and checks live around `recommendation_agent.py`, `recommendation_critic_agent.py`, `market_regime_gate_agent.py`, `investment_committee_agent.py`, and `portfolio_risk_manager_agent.py`.
- Fund-style consensus/replay agents produce research signals only; they must stay subordinate to validation, risk, and committee gates.

## Verification
- Use the smallest meaningful gate for the change: targeted unit/import check, `tools/agents/pipeline_smoke_check.py`, a single agent run, or a compact JSON status read.
- Verify business intent, not just command success. For recommendation changes, check gate decisions, market context fields, and audit/committee outcomes.

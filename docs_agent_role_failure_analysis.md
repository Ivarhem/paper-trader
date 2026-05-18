# Agent Role-Failure Analysis

Date: 2026-05-08
Scope: paper_trader historical/paper research organization only. No real trading/order paths.

## Executive summary

The agents did not fail because individual scripts were broken. They failed because the organization lacked enforceable role contracts. New agents were added to solve immediate research problems, but the pipeline did not preserve a strict operating model for artifact ownership, final authority, and escalation. As a result, several agents performed useful local work while drifting outside their expected organizational role.

## Root causes

### 1. Role manifest was descriptive, not enforceable

`configs/research_agents.yaml` described missions, but there was no machine-checked contract for:

- which artifact each agent may write;
- whether the agent is a base writer, overlay/proposal writer, or final writer;
- who owns final authority for `strategy_registry.status`, recommendation bucket, and trade eligibility;
- which agent should escalate structural defects.

Impact: agents could mutate shared state directly if that solved a local problem.

### 2. Shared mutable `/tmp/recommendations_latest.json` encouraged sequential overwrite

Recommendation agents all read and rewrote the same JSON file. That made it easy for critic/regime/risk agents to change action/bucket-like fields instead of attaching named opinions. The final decision path became implicit in execution order rather than explicit in a committee contract.

Target rule: `recommendation_agent` writes base rows; critic/risk/regime agents attach named subdocuments; `investment_committee` alone writes final bucket/trade eligibility.

### 3. Strategy status authority was split by convenience

`strategy_lifecycle`, `active_strategy_balancer`, and `strategy_tail_risk_filter` all had reasons to change status. Each reason was locally sensible, but together they created unclear authority and possible demote/promote churn.

Target rule: `strategy_lifecycle` is the canonical `strategy_registry.status` writer. Balancer/tail-risk/success-optimizer emit proposals, tiers, or flags unless explicitly run in legacy apply mode.

### 4. Org Evaluator evaluated operational metrics before organizational design

The evaluator focused on validation coverage, active count, committee behavior, stale outputs, and gates. It lacked structural checks for role clarity, toolbox-vs-scheduled separation, authority overlap, and executive-layer effectiveness.

Impact: the exact organizational mess Bayman expected it to catch was invisible until manually noticed.

### 5. Orchestrator and evaluator responsibilities were blurred

The orchestrator originally behaved like a recorder/runner; the evaluator behaved like a metric auditor. Neither strongly owned the executive questions:

- Orchestrator: “What bounded research actions should we run next to improve paper returns?”
- Evaluator: “Is the organization itself correctly structured and doing its job?”
- Guardian: “Which proposed improvements are safe to auto-apply, observe, or require approval?”

### 6. Scheduled vs toolbox agents were mentally mixed

Useful scripts outside the 15-minute organization were not labeled as toolbox/manual/deprecated candidates. This made the org feel larger and less coherent than the scheduled operating model.

### 7. Config drift exposed missing single source of truth

`configs/org_profile.json` says strategy target_active is 7, while the scheduled pipeline still calls active balancer with `--target-active 5`. This is not immediately harmful, but it is evidence that goals live in more than one place.

## Fixes already applied

- `active_strategy_balancer` now defaults to promotion proposal-only; direct status apply requires `--apply-promotions`.
- `strategy_tail_risk_filter` now defaults to status proposal-only; direct demotion requires `--apply-status`.
- `recommendation_critic` no longer changes action directly; it attaches critic warnings/notes.
- `market_regime_gate` no longer changes action directly; it emits regime gate context/proposals.
- `investment_committee` remains the final recommendation bucket/trade eligibility writer.
- `org_evaluator` now performs structural audits and inspects pipeline/source to distinguish real writer conflicts from harmless overlay agents.

## Remaining improvements

1. Add machine-readable authority contracts to `configs/research_agents.yaml`.
2. Add an integrity/evaluator check that flags contract violations when an overlay agent writes final fields.
3. Make `research_pipeline_agent.py` consume `configs/org_profile.json` for target_active/max_promote/high_upside_slots instead of hard-coded values.
4. Add UI org map showing scheduled/toolbox/governance and final authority per artifact.
5. Consider moving recommendation overlays into separate files or a named `opinions` block to reduce accidental overwrites.

## Current health signal

After first-pass fixes, Org Evaluator reported healthy with score 82/100. Remaining issues are mostly watch-level: toolbox labeling, medium validation coverage, and active pool size.

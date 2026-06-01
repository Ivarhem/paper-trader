# Domain Supervisor Refactor

Paper/historical research only. No real orders or broker endpoints.

## Decision

The organization now has a first phase of domain supervisor agents above the existing micro-agents:

- `data_steward` manages universe, price freshness, disclosure, seed data, and data repair queues.
- `market_context_director` manages market issue, regime, route, and recommendation-context routing.
- `strategy_director` manages strategy generation, validation, lifecycle, audit, and duplicate strategy work.
- `fund_director` manages paper fund simulation/replay, registry, performance, risk, and consensus quality.
- `recommendation_desk_lead` manages recommendation generation, critique, committee posture, validation, and outcome feedback.
- `governance_director` manages integrity checks, organization review, experiment workflow, and patch proposal ownership.
- `executive_director` manages the domain supervisors, compact sub-organization health, escalation, and next-cycle priorities.

These supervisors do not directly approve trades, mutate strategy state, or weaken gates. Their job is to emit a compact management contract with:

- `domain_status`
- `owned_agents`
- `role_fitness`
- `duplicate_work`
- `missing_capability`
- `bottleneck`
- `next_cycle_assignments`
- `authority_boundary`

## Phase 2 expansion

Phase 1 introduced strategy, fund, recommendation, and executive supervision because they were closest to Bayman-visible recommendation quality. Phase 2 adds data, market context, and governance supervisors so every major sub-organization has an explicit management contract before the executive director summarizes org health.

Remaining future expansion should be narrower: add an execution/operations director only if cron/runtime ownership keeps producing ambiguous handoffs.

## Boundaries

- Data Steward can flag freshness/quality repair work but does not rewrite datasets directly.
- Market Context Director can route issue/regime context into research queues but does not silently veto recommendation/fund outputs.
- Strategy Director can identify duplicate strategy-state writers but `strategy_lifecycle` remains canonical.
- Fund Director can flag allocation/risk guardrails but fund risk does not become a standalone trade rejection signal.
- Recommendation Desk Lead can enforce opinion separation between critic/risk/regime/committee but final recommendation fields remain owned by the existing recommendation/committee flow.

- Governance Director can own integrity and organization-review queues but keeps patches proposal-only unless an existing guarded agent applies them.
- Executive Director does not override domain decisions; it escalates bottlenecks and keeps the org-level next-cycle priorities explicit.

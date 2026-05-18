# Current Research Organization Structure Review

Date: 2026-05-08
Scope: historical/paper research only.

## Current shape

The scheduled organization now has 44 pipeline steps across five functional layers:

1. Data & Evidence
   - universe/price/disclosure/financial/data-quality/curation.
2. Strategy Research
   - candidate generation, validation, novelty pruning, lifecycle, active-pool proposals, tail-risk tiers, optimizer/audit.
3. Market Context
   - broad market context, dynamic market issues, news issues, narrative, regime gate overlay.
4. Recommendation Decision
   - base recommendations, disclosure-aware rerun, critic/risk/regime overlays, investment committee final decision, shadow/internal boards, feedback/calibration.
5. Meta Governance
   - integrity, org evaluator, guardian, alpha orchestrator.

All scheduled steps are now mapped in `configs/research_agents.yaml`. Role summaries and pipeline config coverage are complete. Authority contracts are explicit:

- `strategy_lifecycle` is canonical strategy status writer.
- `active_strategy_balancer` and `strategy_tail_risk_filter` are proposal/tier agents by default.
- `recommendation_agent` writes base rows.
- `recommendation_critic`, `portfolio_risk_manager`, and `market_regime_gate` write overlays/opinions.
- `investment_committee` writes final recommendation bucket and trade eligibility.

## What improved

- Structural authority conflicts are removed from Org Evaluator findings.
- Guardian no longer has approval-required structural items.
- Pipeline smoke and paper-only integrity pass.
- Orchestrator agenda now focuses on active pool, recommendation conversion, and strategy quality, not already-resolved org authority issues.

## Remaining improvement opportunities

### 1. Toolbox inventory should be first-class

There are still 15 useful scripts outside the scheduled organization. They are not necessarily a problem, but they should be explicitly labeled as Toolbox / Manual Research / Import Utility / Deprecated Candidate.

Recommended next step: add `toolbox_agents` metadata to `configs/research_agents.yaml` and show it in the org UI.

### 2. Active pool target vs quality reality

`org_profile.strategy.target_active` is 7, but current active count is 2 and balancer finds 0 qualified promotion proposals. This is not a pipeline failure; it means target capacity exceeds current validated strategy quality.

Recommended next step: keep target as aspiration, but add an `active_pool_gap` panel that separates:

- target active count;
- qualified promotion proposals;
- blocked candidates and their dominant blockers;
- which validator/optimizer action would unblock them.

### 3. Feedback loop is wide but not prioritized enough

The organization has many feedback agents, but the executive agenda should rank bottlenecks by expected impact. Right now it correctly says conversion/quality are issues, but the next action could be sharper: select top blocked candidate families and assign validation/exit-policy retests.

Recommended next step: make Alpha Orchestrator emit `assigned_research_tasks` with owner agent, target artifact, expected unblock condition, and due horizon.

### 4. Shared recommendation JSON remains a practical risk

Authority contracts guard against final-field overwrites, but overlays still mutate the same JSON file. This is workable but fragile.

Recommended next step: medium-term refactor to store overlays in `recommendation_opinions` or separate latest files, then have committee assemble final cards.

### 5. Meta-governance has four agents and should stay clearly split

Current split is sensible:

- Integrity: runtime/config/safety checks.
- Evaluator: structural and outcome audit.
- Guardian: safe/approval classification of fixes.
- Orchestrator: action agenda and bounded research execution.

Recommended next step: add a small UI legend and an evaluator check that flags if any governance agent starts doing another governance agent's job.

## Recommendation

Do not add more scheduled agents yet. The best next improvements are clarity and bottleneck targeting:

1. Add toolbox classification and org UI map.
2. Add active-pool gap/bottleneck diagnostics.
3. Upgrade Alpha Orchestrator from agenda list to assigned research tasks.
4. Later, split recommendation overlays out of the shared final JSON.

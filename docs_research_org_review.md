# Paper Trader Research Organization Review

Generated: 2026-05-08
Scope: historical/paper research only. No real trading/order paths.

## Diagnosis

The organization has grown by accretion: agents were added to solve immediate problems, but the operating model did not keep a stable hierarchy of responsibilities. The result is a pipeline with many useful components but unclear ownership:

- 44 scheduled pipeline steps plus 58 agent scripts exist.
- Some scheduled steps lacked role summaries (`discovery_validation`, `recommendation_agent_after_disclosure`, `oversold_recovery`).
- Several scripts are useful tools but are outside the scheduled org (`stock_research_run`, `walk_forward_agent`, `symbol_review_agent`, `universe_scout`, etc.).
- Some roles overlap: lifecycle/balancer/tail-risk/success-optimizer all affect strategy status; critic/risk/regime/committee all affect recommendations.
- The old orchestrator acted as a runner/recorder, not an executive that chooses alpha-improvement actions.

## Target Operating Model

### 1. Executive Layer

**Alpha Orchestrator**
- Owns the question: “what should the research org do next to improve paper/historical returns?”
- Produces `alpha_agenda` with priorities, objectives, reasons, and bounded safe actions.
- May run low-risk diagnostics/validation actions.
- Must never place trades or touch brokerage/order paths.

**Org Evaluator**
- Audits organization health, stale gates, throughput, disclosure quality, and committee behavior.
- Should evaluate the org, not run the org.

**Org Improvement Guardian**
- Applies only reversible low-risk maintenance.
- Escalates structural changes as proposals.

### 2. Data & Evidence Layer

Owns freshness and input reliability.
- universe discovery/curation
- price refresh/data quality
- disclosures/financials
- market/news/context collection

### 3. Strategy Research Layer

Owns hypothesis generation, validation, de-duplication, lifecycle, and risk tiering.
- strategy generator
- validation workers/capacity planner
- novelty pruner
- lifecycle/balancer/tail-risk/success optimizer
- exit policy optimizer

Clear rule: only one component should have final write authority for `strategy_registry.status` per phase. Others should propose tiers/flags unless explicitly designated.

### 4. Recommendation Decision Layer

Owns current recommendations and committee-style decisioning.
- recommendation agent
- disclosure re-run + recommendation re-rank
- critic/risk/regime/committee
- shadow/internal signal board

Clear rule: recommendation support, risk approval, and trade eligibility remain separate.

### 5. Feedback & Learning Layer

Owns realized outcome measurement and calibration.
- current recommendation validation
- outcome tracker
- committee ledger
- recommendation funnel/calibration
- sampler/auditor/outcome attribution

## Immediate Findings

1. **Orchestrator role was too weak**
   - Fixed first pass: now emits `alpha_agenda` and runs safe bounded actions.

2. **Strategy status ownership is messy**
   - `strategy_lifecycle`, `active_strategy_balancer`, `strategy_tail_risk_filter`, and `strategy_success_optimizer` can all influence active viability.
   - Recommended cleanup: lifecycle is the only direct status writer; balancer proposes promotions; tail-risk writes tier/flags; success optimizer writes gates/action plan.

3. **Recommendation gate ownership overlaps**
   - critic, portfolio risk, market regime, and committee all mutate recommendation outputs.
   - Recommended cleanup: recommendation agent generates base rows; each gate writes named subdocuments; committee is the final aggregator.

4. **Scheduled vs toolbox agents are mixed mentally**
   - Some scripts should remain toolbox/manual (`stock_research_run`, `symbol_review_agent`, `walk_forward_agent`, importers).
   - The UI should label “scheduled org” vs “toolbox agents”.

5. **Governance visibility was weak**
   - Fixed first pass: org menu now shows Guardian improvement state and Alpha Agenda.

## Recommended Refactor Plan

### Phase A — Make roles explicit, no behavior risk
- Add missing role summaries and timeout config.
- Update manifest with five-layer org model.
- Add org audit output showing scheduled/toolbox/overlap groups.

### Phase B — Reduce status mutation conflicts
- Convert tail-risk/success-optimizer/balancer outputs to proposals where possible.
- Keep lifecycle as canonical `strategy_registry.status` writer.
- Add `strategy_registry.tier` or summary fields for risk/optimizer tiers instead of changing status repeatedly.

### Phase C — Normalize recommendation mutation
- Move all recommendation gates into named fields (`critic`, `risk_manager`, `regime_gate`, `committee`).
- Make committee final aggregation explicit.
- Avoid sequential silent overwrites of the same recommendation fields.

### Phase D — UI org map
- Add a five-layer org map in the 조직 평가 menu.
- Show each agent as Scheduled / Toolbox / Governance / Deprecated candidate.
- Show final authority per artifact: strategy status, recommendation bucket, trade eligibility, score boost.

## Current Low-Risk Changes Already Applied

- Alpha Orchestrator introduced and added to pipeline.
- Org API updated to prefer latest `/tmp` outputs.
- Guardian and Alpha Agenda cards added to org UI.
- Active research floor restored to 2 paper strategies.

# Stock Research Organization

`paper_trader`는 단일 전략 스크립트가 아니라 작은 투자 리서치 조직처럼 운영한다.

## Roles

- Universe Curator: 기존 universe를 active/watch/quarantine/retired로 관리하고 위험/죽은 종목을 격리한다.
- Universe Scout: active universe에서 모멘텀/거래량/공시 기반 신규 후보를 발굴한다.
- Data Agent: 가격 데이터, OpenDART 공시, 종목 메타데이터를 갱신한다.
- Disclosure Analyst: 공시 이벤트를 요약하고 high/medium/positive risk signal을 만든다.
- Strategy Researcher: 기존 walk-forward agent로 전략 후보를 만든다.
- Skeptic Agent: 거래 수 부족, buy-and-hold 미달, 과최적화 의심을 공격한다.
- Risk Manager: drawdown, 공시 veto, 데이터 부족을 기준으로 reject/watch/promote 의견을 낸다.
- Portfolio Manager: 후보 간 중복/상관/집중 위험을 점검한다. 현재는 skeleton.
- Adaptive Investment Committee: 6개 평가 성향 pool(`upside_hunter`, `risk_guardian`, `evidence_skeptic`, `balanced_allocator`, `regime_specialist`, `research_advocate`)로 추천 후보를 평가하고 weight 기반 종합 판단을 낸다. `upside_hunter`는 매수/상승 근거를 먼저 찾고, `research_advocate`는 신규 가설과 under-validated 후보를 연구 큐로 올린다. `risk_guardian`/`evidence_skeptic`은 paper-buy 승인 게이트를 맡아 연구 지지와 매수 승인을 분리한다.
- Committee Performance Ledger: 위원회 history와 recommendation history, 가격 DB를 연결해 future paper outcome을 추적한다.
- Org Evaluator: pipeline/validation/committee 상태를 감시하고 조직 개선 findings를 낸다.
- Org Improvement Guardian: Org Evaluator findings를 observe/manual_review/approval_required/low-risk auto-apply로 분류하고, 안전한 self-healing만 자동 적용한다.

## Guardrails

- 실거래 없음. 결과는 research recommendation/paper review다.
- cutoff 이전 데이터만 전략 선택에 사용한다.
- 공시는 cutoff 이전 이벤트만 gate/feature로 반영한다.
- positive disclosure는 자동 promote가 아니라 supporting context다.
- high-risk disclosure는 veto 권한을 가진다.
- 조직 개선안은 기본적으로 제안/감시 레이어다. 자동 적용은 low-risk reversible maintenance로 제한한다.
- 전략 임계값, evaluator 추가/제거, pipeline topology, 외부 서비스 변경, destructive change는 승인 필요다.
- Adaptive Committee weight learning은 보수적으로 수행하며, audit proxy/future paper outcome 기반으로 작은 폭만 조정한다.

## Output

```text
/tmp/stock_research_org_latest.json
/tmp/research_pipeline_latest.json
/tmp/investment_committee_latest.json
/tmp/investment_committee_weights.json
/tmp/investment_committee_history.json
/tmp/committee_performance_ledger_latest.json
/tmp/research_org_evaluation_latest.json
/tmp/org_improvement_guardian_latest.json
```

Monitor/API는 이 파일을 읽어 조직형 research 상태를 보여준다.


## Current 15-minute pipeline

The scheduled production loop uses `scripts/run_research_org_cron.sh`, which runs `tools/agents/research_pipeline_agent.py` under a flock lock. The status files are:

```text
/tmp/research_pipeline_status.json
/tmp/research_pipeline_latest.json
logs/research_pipeline_cron.log
```

Current pipeline includes strategy generation, walk-forward/audit, novelty pruning, strategy lifecycle/balancing, recommendations, regime gate, critic/risk checks, adaptive committee, committee ledger, org evaluator, and org improvement guardian.

## Improvement application policy

Org menu recommendations are not blindly applied. The guardian classifies each finding:

- `observe`: tracked in reports; no immediate action.
- `manual_review`: requires human/Ray review.
- `approval_required`: requires explicit approval before structural change.
- low-risk auto-apply: reversible maintenance only, with rollback information stored.

This keeps research automation useful without letting it silently change strategy policy.


## Historical validation success optimizer

`strategy_success_optimizer_agent.py` is a reader of Simulation Validation Worker history (`recommendation_validation_results`). It does not assess real-trading readiness. Its strictest tier is `high_confidence_historical`: a paper/historical validation grade used to decide whether a strategy can produce candidate-buy-zone research output or should remain watch-only.

## Orchestration operating model

Main Ray should stay the orchestrator/reviewer for repo and operations work. Work
that may run longer than about two minutes, retry, or touch multiple modules
should be delegated to a bounded worker/isolated session with explicit file
ownership and verification gates. Shared status is recorded by
`scripts/agent_task_state.py` in `state/agent_tasks.json` and
`/tmp/agent_task_state_latest.json`; see
`docs/agents/multi_agent_orchestration.md`.

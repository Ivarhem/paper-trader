# 상세 발견사항 (Findings)

> 각 항목: **현상 → 근거(file:line) → 영향 → 개선 제안**. 개선안은 제안이며 코드 미수정.
> 심각도: 🔴 데이터 정합성/조직 통제 직접 영향 · 🟠 유지보수성/확장성 · 🟡 위생/일관성.

---

## F1 🔴 암묵적 파일 기반 의존성 — stale 데이터 무방비

**현상.** 파이프라인 단계 간 데이터 의존이 명시적 DAG가 아니라 `/tmp/<key>_latest.json` 경로 문자열로만 연결된다. 상류 단계가 실패해 파일을 갱신하지 못해도, 하류 단계는 **직전 사이클이 남긴 stale 파일** 또는 `{'_read_error': ...}` 딕셔너리를 정상 입력처럼 읽어 처리한다.

**근거.**
- 생산/소비 페어: `research_pipeline_agent.py` 421~423행 부근 — `recommendation_agent`가 `/tmp/recommendations_latest.json` 생산, 직후 `disclosure_impact_recommendations`가 `--recommendations /tmp/recommendations_latest.json`로 소비.
- 읽기 실패가 데이터로 둔갑: `lib/runtime.py`의 `read_json()`이 결손/에러 시 `{}` 또는 `{'_read_error': ...}` 반환(예외 전파 안 함).
- 신선도 검증 부재: 단계 실행 전 "상류 아티팩트가 이번 사이클 산출물인가"를 확인하는 코드 없음.

**영향.** 한 상류 에이전트의 조용한 실패가 하류 추천/감사로 **무경고 전파**된다. 운영자는 파이프라인이 `ok`로 끝났다고 보지만 실제 추천은 N시간 전 데이터 기반일 수 있다. 데이터 정합성 리스크 1순위.

**개선 제안.**
1. (저위험·우선) 소비 매트릭스 문서화: 각 단계의 `consumes`/`produces` 아티팩트 표를 만들어 의존을 가시화.
2. 아티팩트에 `run_at`/`source_run_id` 스탬프(이미 계약 봉투에 `run_at` 있음)를 *읽는 쪽에서* 검증 — 상류가 이번 사이클에 안 돌았으면 하류 결과에 `stale_input` 경고 플래그를 달고 기록.
3. `block` 등급 상류(`recommendation_agent` 등)는 실패 시 하류를 skip하도록 의존 게이트 추가(점진).

---

## F2 🔴 하드코딩된 단계 리스트 (god-function), config는 정책만

**현상.** 89개 단계의 순서·CLI 인자·조건부 포함이 `research_pipeline_agent.py:main()` 안 **92개 `add()` 호출**에 하드코딩되어 있다. config `research_pipeline.yaml`은 로드되지만(`load_pipeline_config()` 173행, 호출 300행) 제공하는 것은 **per-agent `timeout_seconds`와 `failure_mode`뿐**. 순서/선택/인자/조건은 코드에만 존재.

**근거.**
- `add()` 클로저 정의 307행, 호출 315~486행(92회: `grep -c "add(" → 92`).
- 조건 분기 코드 내재: `if not args.skip_data_refresh`(319/327), `if args.include_heavy_retests`(412), `if logic_csv`(461), `validation_first_mode`/`repeat_suppression_mode` 휴리스틱(349~440).
- config 스키마는 `{"agents": {name: {timeout_seconds, failure_mode}}}` 구조뿐 — 순서/의존 필드 없음.

**영향.** "validation만 돌리기", "market context 그룹만 재실행", 단계 재배열, 신규 단계 삽입에 **모두 Python 코드 수정** 필요. 테스트·실험·부분 운영이 어렵고, 거대 `main()`이 변경 위험의 단일 집중점.

**개선 제안.**
- `research_pipeline.yaml`을 단계 매니페스트로 확장: `order`, `args`, `consumes`, `produces`, `when`(조건) 필드. orchestrator는 매니페스트를 순회 실행하는 얇은 엔진이 됨.
- 빅뱅 금지 — **신규 단계부터 config-driven**으로 추가하고 기존 단계는 점진 이전. F1의 의존 매트릭스와 동일한 `consumes/produces` 어휘를 재사용하면 두 작업이 합쳐진다.

---

## F3 🟠 권한 계약이 선언만, 코드 집행 없음

**현상.** "누가 무엇을 쓸 권한이 있는가"가 풍부하게 *문서화*되어 있으나 코드가 강제하지 않는다. 위반은 `org_evaluator`의 **사후 감사**로만 검출된다.

**근거.**
- 선언: `docs_current_org_structure_review.md`의 authority 계약("`strategy_lifecycle`가 strategy status 정본 writer", "`investment_committee`만 최종 recommendation bucket/trade_eligible 작성"), `domain_supervisor_lib.py`의 `manager_evaluation_only_no_direct_trade_or_status_mutation` 가드레일.
- 사후 감사: `org_evaluator_agent.py`의 `structural_org_audit`가 "strategy_status_writers_or_influencers" 중복을 *발견·보고*하지만, 막지는 못함.
- 집행 부재: strategy status / recommendation bucket을 쓰는 DB helper에 writer 화이트리스트 없음 — 어느 에이전트든 호출 가능.

**영향.** `active_strategy_balancer`/`strategy_tail_risk_filter`/`strategy_success_optimizer`가 strategy 상태에 영향, critic/risk/regime이 recommendation 필드에 영향 — 정본 writer와 책임 경계가 흐려져 promote/demote churn·책임 불명확. 디렉터 권한도 "제안만 가능, 거부권 없음"이라 조직도가 실제 통제력을 갖는지 검증 불가.

**개선 제안.**
1. (우선) 현행 *실제* writer 매핑 감사 — 선언된 정본 writer 외에 누가 해당 필드/테이블을 쓰는지 grep·런타임 확인.
2. DB write helper에 writer 화이트리스트 게이트(예: `set_strategy_status(writer=...)`가 `strategy_lifecycle` 외 호출 시 거부/경고). 우선 경고 모드 → 검증 후 강제.

---

## F4 🟠 트리플-라이트, 단일 진실원(SoT) 모호

**현상.** 동일 아티팩트가 SQLite `latest_artifacts`(정본 설계)·`/tmp/*_latest.json`(프로세스 소유, 가장 신선)·`static/*_latest.json`(미러) 3곳에 쓰인다. `sync_latest_artifacts.py`가 파일→DB 동기화를 하지만 시점 차로 채널 간 불일치 창이 생기고, 읽기 폴백 체인이 모듈마다 미묘히 다르다.

**근거.**
- 쓰기 3종: `lib/runtime.py`의 `write_json`(DB+/tmp) / `write_json_and_static`(DB+/tmp+static), `lib/agent_contract.py`의 `write_json_shared`(/tmp 우선, DB는 try/except).
- 읽기 폴백: `app/main.py read_tmp_json(prefer_db=True)` vs `lib/runtime.py read_json` — DB·/tmp·static 우선순위 처리가 일관되지 않음.
- static 40개는 수동 큐레이션 미러(자동 동기화 아님).

**영향.** DB가 정본이라 API는 DB를 읽지만 `/tmp`가 더 신선할 수 있어, 동기화 지연 동안 UI/소비 에이전트가 옛 데이터를 본다. 어느 채널이 진실인지 상황에 따라 달라짐.

**개선 제안.**
- 정본을 한 채널로 못박고(권장: DB), 나머지는 명시적 파생으로 규정. 읽기 경로를 `lib/runtime.py` 단일 함수로 통일(F9와 연계).
- 쓰기 시 `payload_hash`로 DB↔파일 불일치 감지·경고. static 미러는 "표시 전용, 의사결정 입력 금지" 라벨.

---

## F5 🟠 메타-거버넌스 산출 추적 부재 (오버엔지니어링 의심)

**현상.** 조직 자기개선 루프가 3계층(`org_evaluator` → `org_improvement_guardian` → `org_architecture_review` → `research_org_orchestrator` 라우팅)이나, 생성된 proposal이 실제 **적용/반려되었는지 audit trail이 없다.** guardian의 `auto_apply` 분류도 실행 로그가 없어 "분류만 하고 끝"인지 확인 불가.

**근거.**
- `org_evaluator_agent.py`(585줄)가 다수 proposal + health_score 생성, `org_improvement_guardian_agent.py`가 observe/manual_review/approval_required/auto_apply로 분류, 이력은 `/tmp/org_improvement_guardian_history.json`.
- 그러나 "어떤 proposal이 어느 사이클에 적용됨/반려됨" 결과 ledger 부재. `org_evaluator`와 `org_architecture_review`는 둘 다 구조 감사를 수행 — 역할 경계 모호.

**영향.** 거버넌스 기계의 비용(매 사이클 3개 에이전트)이 측정 가능한 산출로 이어지는지 불투명. proposal이 orphan 상태로 반복 생성될 위험.

**개선 제안.**
- guardian 결정 + 후속 적용 여부를 durable ledger(`/tmp` 아닌 DB 테이블)로 기록 — "제안→상태→결과" 추적.
- `org_evaluator` + `org_architecture_review` 통합 검토(둘 다 구조 감사). 단, 다른 cadence(매 사이클 vs 주간)로 분리할 명분이 있으면 유지.

---

## F6 🟠 거대 에이전트 + 변종 분기 = 파라미터화 부재

**현상.** `recommendation_agent.py`가 **2949줄**, `recommendation_auditor.py` 1333줄, `investment_committee_agent.py` 1656줄. 또한 `recommendation_agent_after_disclosure`라는 *변종*이 별도 단계로 존재 — 동일 로직을 disclosure 반영 후 재실행하는 형태로, 파라미터화로 흡수 가능해 보임.

**근거.**
- `wc -l`: `recommendation_agent.py` 2949, `recommendation_auditor.py` 1333, `investment_committee_agent.py` 1656, `paper_fund_price_replay_agent.py` 1016.
- `research_pipeline_agent.py`가 같은 스크립트를 다른 인자로 두 번(base / after_disclosure) 호출.

**영향.** 기반 로직 변경 시 cascading 수정 위험, 리뷰·테스트 난이도, 변종 간 드리프트.

**개선 제안.** (P3, 기능 동일 리팩토링)
- `recommendation_agent` 공통 코어 추출 → `_after_disclosure`를 `--phase=after_disclosure` 플래그로 흡수.
- 2949줄 내부의 파라미터 로딩(target-return, strategy router, fund consensus 로딩 등 복붙 단편)을 `lib/`로 추출.

---

## F7 🟠 에이전트 클러스터 중복

**현상.** 유사 목적 에이전트군이 로직의 상당부(추정 60~80%)를 공유.

**근거(클러스터).**
- **market scout 계열**: `market_issue_scout`, `market_news_issue_scout`, `market_shock_mover_scout`, `market_mover_seed`, `kr_entry_signal_scout`, `kr_multi_evidence_signal_scout` — 시장 이슈/무버 탐지로 겹침.
- **validation worker 계열**: `simulation_validation_worker`, `current_recommendation_validation_worker`, `external_mover_validation_agent`, discovery validation — 모두 후보집합에 대한 과거 백테스트 검증.
- **parameter evaluator 계열**: `supply_weight_evaluator`, `investor_flow_outcome_evaluator`, `target_return_adjustment_evaluator` — 파라미터 튜닝.

**영향.** 변경 시 N곳 동기화, 경계 모호, 인지 부하.

**개선 제안.** (P3) 각 클러스터를 `--mode` 파라미터 단일 에이전트로 통합 검토(예: `validation_worker.py --mode {recs|discovery|simulation|external}`). 통합 전 **입력/출력 계약이 실제로 동형인지** 먼저 확인 — 동형 아니면 통합이 오히려 복잡도 증가.

---

## F8 🟡 config 키 ↔ 파일명 불일치

**현상.** `research_agents.yaml`의 에이전트 키와 실제 파일명이 암묵 매핑. 예: `capacity_planner` → `validation_capacity_planner.py`, `opendart_disclosures` → `opendart_disclosure_agent.py`. 접미사도 `_agent.py`/`_worker.py`/`_auditor.py`/접미사 없음이 혼재.

**근거.** `research_agents.yaml`에 95개 이름 등재, `tools/agents/`에 108개 파일 — 1:1 자명 매핑 아님. 키→파일 해석이 orchestrator의 `add()` 인자에만 존재.

**영향.** config만 보고 파일을 못 찾음(IDE 점프 불가), config 유지보수 시 오타·미스매핑 위험.

**개선 제안.** 명명 규약 표준화(`{name}_agent.py` 권장) 또는 config에 `module:` 경로 필드 명시. F2 매니페스트 작업과 함께 처리하면 자연스럽다.

---

## F9 🟡 공유 라이브러리 저활용

**현상.** `lib/runtime.py`가 DB우선 read + 파일/미러 write 추상화를 제공하나, 다수 에이전트가 이를 쓰지 않고 읽기 패턴을 copy-paste한다. `attach_contract()`(봉투)는 채택도 높음.

**근거.** `lib/` 7개 모듈 중 `runtime.py`의 `read_json/write_json`은 소수만 사용(탐색 결과). `recommendation_agent.py` 내 target-return/strategy router/fund consensus 로딩이 lib 미사용 복붙.

**영향.** 읽기 폴백 동작이 호출처마다 미묘히 달라짐(F4와 상호 악화), 버그 수정이 일부에만 반영.

**개선 제안.** 모든 아티팩트 read/write를 `lib/runtime.py` 단일 진입점으로 의무화. 반복 로딩 단편을 lib 헬퍼로 추출.

---

## F10 🟡 아티팩트 스키마 미강제

**현상.** `agent_contract.v1` 봉투(필수 필드 정의)는 있으나 **read 시 검증이 없다.** 또한 status가 `.contract.status`와 `.status` 두 형태로 공존해 소비측이 방어적 분기를 해야 함.

**근거.**
- `lib/agent_contract.py`에 스키마 문자열·필수 필드 정의, 그러나 JSON Schema 파일·검증 함수 없음.
- 이중 형태: `scripts/agent_task_state.py`가 `(data.get("contract") or {}).get("status") ... else data.get("status")`로 양쪽 처리.
- 스키마 버전 `v1` 고정, 마이그레이션 경로 없음.

**영향.** 계약 드리프트가 조용히 누적, 소비측 방어 코드 증식.

**개선 제안.** read 시 봉투 필수 필드 1회 검증하는 얇은 게이트(우선 경고). status 형태를 한쪽으로 정규화. 차후 `schema_version` 도입.

---

## F11 🟡 리포트가 단일 거대 dict 표현식

**현상.** `research_pipeline_agent.py`의 최종 리포트가 **한 줄짜리 거대 dict 리터럴**(중첩 comprehension 다수)로 작성됨.

**근거.** `research_pipeline_agent.py:610` — `report={...}`가 수십 개 중첩 필드·comprehension을 단일 표현식에 담음.

**영향.** 가독성·diff·디버깅 난이도. 필드 추가 시 실수 유발.

**개선 제안.** 빌더 함수/단계별 dict 조립으로 분해(기능 동일 리팩토링).

---

## F12 🟡 인벤토리 위생 — 미분류 toolbox/orphan

**현상.** 108개 파일 중 메인 오케스트레이터가 직접 호출하는 건 89개. 나머지 ~19개는 별도 cron·수동 실행·라이브러리·orphan이 섞여 있으나 라벨이 없다.

**근거.**
- `tools/agents/*.py` 108개 vs orchestrator 참조 89개 vs config 등재 95개 — 세 숫자가 불일치.
- 좁은 참조 프로브에서 메인 파이프라인/크론/config 어디서도 미참조: `pt_review.py`(그 외 `domain_supervisor_lib.py`는 라이브러리라 정상).
- 별도 cron: `run_validation_worker_cron.sh`, `run_next_trade_issue_context_cron.sh` 등 5개가 일부 에이전트를 독립 호출.

**영향.** "이 스크립트가 운영 중인지/폐기인지" 판단 불가, 사프롤. 기존 `docs_current_org_structure_review.md`도 "15개 비스케줄 스크립트는 Toolbox/Manual/Deprecated 라벨이 필요"라고 동일 지적.

**개선 제안.** 각 에이전트에 분류 라벨 부여: `SCHEDULED`(메인 파이프라인) / `CRON-STANDALONE` / `TOOLBOX-MANUAL` / `LIBRARY` / `DEPRECATED`. `tools/agents/README.md` 또는 config에 1열 인벤토리 표로 기록. `pt_review.py` 등 미참조 항목은 용도 확인 후 라벨링/아카이브.

---

## 부록: 잘 되어 있는 점 (유지 권장)

- **안전 경계**: "no real orders / paper-historical only"가 문서·config·코드 전반에 일관. 약점 미발견.
- **도메인 슈퍼바이저 패턴**: 89개 산출물을 6개 디렉터 요약으로 압축하는 토큰 효율 triage 설계는 규모 대비 합리적.
- **느슨한 결합**: 웹앱(A)과 에이전트(B)가 아티팩트로만 결합 — 독립 배포·테스트 용이(결합 매개의 취약성은 F1/F4로 별도 관리).
- **실패 등급화**: `block`/`degrade` 구분 + flock + 부하/일시정지 가드로 cron 안정성 확보.
- **read-order 규약**: `AGENTS.md`의 compact-first 읽기 지침은 LLM 컨텍스트 예산 보호에 효과적.

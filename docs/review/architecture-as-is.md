# 현행 아키텍처 맵 (As-Is)

> 이 문서는 "현재 어떻게 동작하는가"의 사실 기록이다. 평가·개선안은 [findings.md](findings.md)에.

## 1. 큰 그림: 3개의 실행 표면(execution surface)

paper_trader는 사실상 세 종류의 시스템이 한 디렉터리에 공존한다.

```
┌────────────────────────────────────────────────────────────────┐
│ (A) FastAPI 웹앱 (app/)                                          │
│   - 실제 paper-trading: watchlist, prices, signals, trades       │
│   - 모니터 UI(static/) 서빙, 인증(auth_users.json + pt_session)  │
│   - 에이전트가 생산한 아티팩트를 read-only로 노출                │
│   진입점: app/main.py (1877), app/database.py (1774)             │
├────────────────────────────────────────────────────────────────┤
│ (B) 멀티에이전트 리서치 오케스트레이션 (tools/agents/)           │
│   - 89개 에이전트를 순차 실행하는 cron 파이프라인                │
│   - "투자 조직" 메타포: 디렉터·위원회·거버넌스                   │
│   진입점: tools/agents/research_pipeline_agent.py (642)          │
│   기동: scripts/run_research_org_cron.sh (flock + 부하가드)      │
├────────────────────────────────────────────────────────────────┤
│ (C) 메타-거버넌스 / 자기개선 루프 (tools/agents/org_*)           │
│   - 조직 스스로를 평가하고 개선안을 분류·라우팅                  │
│   진입점: org_evaluator → org_improvement_guardian               │
│            → org_architecture_review → research_org_orchestrator │
└────────────────────────────────────────────────────────────────┘
```

(A)와 (B)는 **아티팩트(JSON/DB)로만 결합**한다 — 에이전트가 쓰고, 웹앱이 읽는다. 직접 함수 호출 없음. 이 느슨한 결합은 장점이지만, 결합 매개가 "경로 문자열"이라 깨지기 쉽다(→ F1, F4).

## 2. 조직 위계 (B의 논리 구조)

`configs/research_agents.yaml`이 정의하는 6개 그룹 + 도메인 슈퍼바이저(디렉터) + 최상위 executive.

```
                        executive_director
        ┌───────────┬───────────┼───────────┬───────────┬───────────┐
   data_steward  strategy_   market_      fund_       recommendation  governance_
   (data office) director    context_dir  director    _desk_lead      director
        │           │           │           │           │             │
  data_ingestion strategy_   market_      fund_       recommendation  meta_
  (~13 agents)   research    context      research    _committee      governance
                 (~19)       (~17)        (~8)        (~18)           (~14)
        └───────────┴───────────┴───────────┴───────────┴─────────────┘
                                    │
                  suborg_summary_agent (440줄) — 6개 디렉터 출력 재집계
                                    │
                  research_org_orchestrator — 어젠다 라우팅(실행 안 함)
```

근거:
- 그룹/미션/멤버: `configs/research_agents.yaml` (520줄, `agent_groups:` 6개)
- 디렉터 가드레일/소유 목록: `tools/agents/domain_supervisor_lib.py` (369줄, `_AGENT_GUARDRAIL_OVERRIDES`, `emit_supervisor()`)
- 관리 트리: `tools/agents/suborg_summary_agent.py` 상단 `MANAGEMENT_TREE`
- executive 집계: `tools/agents/executive_director_agent.py`

### 도메인 슈퍼바이저 패턴 (핵심 추상화)

`domain_supervisor_lib.py`가 제공하는 공통 봉투. 각 디렉터는 자기 그룹 N개 에이전트의 아티팩트를 읽어 단일 "domain_supervisor" 계약(상태·fitness·bottleneck·next_cycle_assignment)으로 압축한다. **89개 산출물을 일일이 안 읽고 6개 디렉터 요약으로 분류(triage)하기 위한 토큰 효율 장치**다. 6/7 디렉터에서 일관 사용됨(`market_context_director`만 탐색 중 접근 이슈로 미확인). 권한 명시: `manager_evaluation_only_no_direct_trade_or_status_mutation` — 디렉터는 평가만, 직접 mutation 금지(문서상).

## 3. 파이프라인 오케스트레이션 (B의 실행 구조)

### 기동 체인
```
cron → scripts/run_research_org_cron.sh
        ├─ batch_pause_guard 확인 (소스 편집 중이면 skip)
        ├─ system load > 4.0 이면 skip
        ├─ flock -n (동시 실행 1개 보장, exit 75 = 다른 인스턴스 실행 중)
        └─ research_pipeline_agent.py (timeout 기본 1800s)
```
근거: `scripts/run_research_org_cron.sh` (lock/load/pause 가드 53~108행 부근).

### 단계 정의 — 하드코딩
- `research_pipeline_agent.py:main()` 내부에서 `add(name, cmd, outputs, required)` 클로저(307행)를 **92회** 호출해 단계 리스트를 누적한다(315~486행).
- 실행은 **완전 순차** — 각 단계는 `subprocess.run(..., timeout=...)`로 블로킹. 병렬 없음. 총 런타임 = 단계 시간 합.
- config(`research_pipeline.yaml`)는 **읽긴 한다**(`load_pipeline_config()` 173행, 300행 호출) — 그러나 **timeout_seconds와 failure_mode(block/degrade)만** 제공. **순서·인자·조건부 포함은 전부 Python 코드에 있다.**
- 조건 분기도 코드에: `if not args.skip_data_refresh`, `if args.include_heavy_retests`, `if logic_csv`, `validation_first_mode`/`repeat_suppression_mode` 휴리스틱(349~440행).

### 실패 모드
- `failure_mode: block`인 소수 에이전트(`pipeline_smoke_check`, `recommendation_agent`, `recommendation_audit`, `strategy_lifecycle`, `data_quality` 등) 실패 → `required` → 파이프라인 종료 시 `exit(1)`.
- 나머지는 `degrade` — 실패 기록하되 진행. **재시도 없음.** 다음 cron 사이클에 전체 재실행.
- 단계 상태: `ok / degraded / failed_required / failed_optional`. `returncode==0`이어도 하류 계약 status가 비-ok면 `degraded`.

### 단계 간 의존 = 파일 경로 컨벤션 (선언 없음)
명시적 DAG가 없다. 예시:
```
add('recommendation_agent', [...], ['/tmp/recommendations_latest.json'])          # 생산
add('disclosure_impact_recommendations',
    [..., '--recommendations', '/tmp/recommendations_latest.json', ...])           # 소비
```
상류가 조용히 실패해 파일을 안 쓰면, 하류는 `read_json()`이 직전 사이클의 stale 파일(또는 `{'_read_error': ...}`)을 읽어 그대로 처리한다 — **에러가 데이터로 둔갑**(→ F1).

### 세 개의 러너 — 중복 아님, 역할 분담
| 러너 | 역할 | 에이전트 실행? |
|------|------|---------------|
| `research_pipeline_agent.py` | cron이 부르는 **실행 오케스트레이터** (89단계) | ✅ subprocess |
| `research_org_orchestrator.py` | 완료된 아티팩트를 읽어 **어젠다를 디렉터로 라우팅** (파이프라인 마지막 단계로 호출됨) | ❌ 메타 라우팅만 |
| `research_org_run.py` / `stock_research_run.py` | 8~10개 역할 함수의 **투자조직 시뮬레이션** (standalone/CLI) | ✅ 함수 호출 |

→ orchestrator는 pipeline_agent의 *소비자*이지 경쟁 오케스트레이터가 아니다. 다만 이름이 비슷해 "어느 게 주(主) 오케스트레이터냐" 혼동 유발.

## 4. 데이터 흐름 / 통신 채널

에이전트 A의 출력이 에이전트 B(또는 웹앱)의 입력이 되는 경로는 **트리플 채널 + DB 우선**.

```
쓰기:  agent → save_latest_artifact(SQLite latest_artifacts)   ← 정본(canonical)
            → write_json(/tmp/<key>_latest.json)               ← 프로세스 소유
            → (선택) static/<key>_latest.json                  ← 오프라인/git 미러
읽기:  latest_artifact(DB, prefer_db) → /tmp 폴백 → static 폴백
```

근거:
- 봉투 정의: `tools/agents/lib/agent_contract.py` (`schema: paper_trader.agent_contract.v1`, 필수 필드 `agent/status/run_at/inputs/outputs/metrics/warnings/next_actions`)
- read/write 추상화: `tools/agents/lib/runtime.py` (`read_json`, `write_json`, `write_json_and_static`)
- DB save/read: `app/database.py` (`save_latest_artifact()` ~1375행, `latest_artifact()` ~1423행)
- API read: `app/main.py`의 `read_tmp_json(..., prefer_db=True)`
- 파일→DB 동기화: `scripts/sync_latest_artifacts.py`

### 채널 4종 정리
| 채널 | 용도 | 동시성 보호 |
|------|------|-------------|
| SQLite `latest_artifacts` / `agent_runs` / `research_org_reports` | **정본**, 인덱스·해시·이력 | busy timeout 45s |
| `/tmp/*_latest.json` | 프로세스 산출, 가장 신선 | tempfile + `os.replace()` 원자적 쓰기 |
| `static/*_latest.json` (40개) | 모니터 UI / git 폴백 미러 | 수동 큐레이션 |
| `state/agent_tasks.json`, `state/batch_pause_guard.json` | 태스크 수명주기·일시정지 ledger | `fcntl.flock()` + `.lock` 파일 |

### 결합 핫스팟 (다수가 읽는 아티팩트)
`recommendations_latest`, `recommendation_audit_latest`, `research_org_suborg_summary_latest`, `market_context_latest`, `fund_consensus_latest`, `strategy_context_router_latest`. 특히 `suborg_summary_agent`는 35+ 아티팩트를 읽는 최대 결합점이고, `recommendation_agent`는 14개를 읽어 추천을 생성한다.

### 공유 라이브러리 (`tools/agents/lib/`)
| 모듈 | 역할 | 채택도(추정) |
|------|------|-------------|
| `agent_contract.py` | 표준 계약 봉투 `attach_contract()` | 높음(대다수 에이전트) |
| `runtime.py` | DB우선 read + 파일/미러 write | **낮음** — 일부만, 다수는 읽기 패턴 copy-paste |
| `pipeline_context_contracts.py` (29KB) | read-order 최적화용 compact 계약 | 소수(suborg/queue) |
| `fx.py` | 환율·KRW 정규화 | 소수 (app/main.py도 import) |
| `indicator_taxonomy.py`, `corporate_actions.py` | 지표 분류 / 기업행동 리스크 | 소수 |

## 5. 안전 경계 (잘 지켜지는 부분)

"historical/paper research only, no real orders"가 `AGENTS.md`, `README.md`, `configs/research_agents.yaml`의 `safety_boundary`, 다수 에이전트 docstring에 일관되게 박혀 있다. 브로커 연동·거래소 키 없음. **이 부분은 리뷰에서 약점이 발견되지 않았다** — 유지 권장.

## 6. 요약: 무엇이 무엇을 통제하는가

| 통제 대상 | 선언 위치 | 집행 위치 | 격차 |
|-----------|-----------|-----------|------|
| 단계 순서/선택 | (없음 — 코드가 곧 선언) | `research_pipeline_agent.py` 92×`add()` | config는 정책만 (F2) |
| 단계 간 의존 | (암묵 — 경로 컨벤션) | 없음 | DAG 부재, stale 무방비 (F1) |
| 에이전트 timeout/실패정책 | `research_pipeline.yaml` | orchestrator | ✅ 정상 |
| 권한(누가 status/bucket을 쓰나) | docstring + `domain_supervisor_lib` 가드레일 | `org_evaluator` 사후 감사만 | 코드 강제 없음 (F3) |
| 아티팩트 정본 | "DB가 정본" (설계) | DB+파일+static 3중 | sync 지연 시 불일치 (F4) |
| 조직 자기개선 | `org_evaluator` proposal | guardian 분류 → director 라우팅 | 적용/반려 추적 없음 (F5) |

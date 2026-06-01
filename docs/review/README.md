# Paper Trader 아키텍처 리뷰 (2026-06-01)

> 범위: **아키텍처 / 에이전트 조직** 중심. 코드 라인별 버그 헌팅이 아니라 구조·설계 품질 리뷰.
> 안전 경계: historical/paper research only — 이 리뷰는 그 경계 자체를 바꾸지 않는다.
> 성격: **문서화 전용. 이 리뷰는 코드를 수정하지 않는다.** 개선안은 제안 상태이며, 실제 적용은 별도 승인 후 진행한다.

## 문서 구성

| 파일 | 내용 |
|------|------|
| [README.md](README.md) | 인덱스 · 요약 · 우선순위 발견사항 · 개선 로드맵 (이 문서) |
| [architecture-as-is.md](architecture-as-is.md) | 현행 아키텍처 맵: 조직 계층, 파이프라인 오케스트레이션, 데이터 흐름 채널 |
| [findings.md](findings.md) | 상세 발견사항(F1~F12) — 심각도 · 근거(file:line) · 개선 제안 |

## 리뷰 방법 및 한계

- 4개 병렬 탐색으로 파이프라인 오케스트레이션 / 조직 설계 / 데이터 흐름 / 에이전트 인벤토리를 매핑한 뒤, 핵심 주장은 직접 코드로 재확인했다.
- 108개 에이전트 중 대표 샘플과 핵심 허브 에이전트(`research_pipeline_agent.py`, `recommendation_agent.py`, `suborg_summary_agent.py`, `org_evaluator_agent.py`, `domain_supervisor_lib.py`)를 정독했고, 나머지는 docstring·시그니처·참조관계 수준에서 확인했다.
- 따라서 **개별 에이전트 내부 로직의 정확성**(예: look-ahead bias, 통계 타당성)은 이 리뷰의 대상이 아니다. 그건 별도 "정확성/로직" 리뷰로 분리하는 것을 권장한다(아래 로드맵 P3).

## 한눈에 보는 규모

| 항목 | 수치 | 근거 |
|------|------|------|
| `tools/agents/*.py` 파일 | **108개** | `ls tools/agents/*.py` |
| 메인 오케스트레이터가 직접 호출하는 에이전트 | **89개** | `research_pipeline_agent.py`의 distinct `tools/agents/*.py` 참조 |
| `research_agents.yaml`에 등재된 에이전트 이름 | **95개** | config 그룹 매니페스트 |
| 파이프라인 단계 정의 `add()` 호출 | **92회** | `research_pipeline_agent.py` |
| `app/main.py` / `app/database.py` | 1877 / 1774줄 | — |
| 단일 최대 에이전트 `recommendation_agent.py` | 2949줄 | — |
| `/tmp`·`state`·`static` 아티팩트 JSON | static만 40개 | 트리플 채널 |

## 종합 평가

조직 구조 자체는 **의외로 일관적이고 잘 설계**되어 있다. 6개 도메인 그룹 + 도메인 슈퍼바이저(디렉터) 패턴 + 권한 계약(누가 무엇을 쓰는가) 문서화 + 토큰 효율적 read-order 규약은 89개 에이전트 규모를 감당하기 위한 합리적 장치다. "no real orders" 안전 경계도 문서·코드·config 전반에 일관되게 박혀 있다.

문제는 구조의 *부재*가 아니라 **두 가지 축의 불일치**다:

1. **선언(declaration)과 집행(enforcement)의 괴리** — 권한 계약·조직 위계·의존 관계가 "문서/메타데이터"로는 풍부하게 선언되어 있으나, **코드 레벨에서 강제되지 않는다.** 파이프라인 순서는 92개 하드코딩된 `add()` 호출에 있고, config(`research_pipeline.yaml`)는 timeout/failure_mode만 제공한다. 권한 경계는 docstring과 `org_evaluator`의 사후 감사로만 존재한다.

2. **메타-거버넌스의 비용 대비 산출 불투명** — 조직이 스스로를 평가·개선하는 루프(`org_evaluator` → `org_improvement_guardian` → `org_architecture_review` → director 라우팅)가 3계층으로 존재하나, 제안(proposal)이 실제로 적용·반려된 추적 기록(audit trail)이 없어 거버넌스 기계의 산출이 측정되지 않는다.

이 둘이 결합되어, "조직도는 정교한데 그 조직도가 런타임을 실제로 통제하는지 검증할 길이 없는" 상태가 핵심 리스크다.

## 우선순위 발견사항 (상세는 [findings.md](findings.md))

| ID | 심각도 | 제목 | 한 줄 요약 |
|----|--------|------|-----------|
| **F1** | 🔴 High | 암묵적 파일 기반 의존성 | 단계 간 의존이 `/tmp/*_latest.json` 경로 문자열로만 연결 — 상류 실패 시 하류는 **직전 사이클의 stale 데이터**를 조용히 소비 |
| **F2** | 🔴 High | 하드코딩된 단계 리스트 (god-function) | 89개 단계 순서·인자·조건이 `main()` 안 92개 `add()`에 하드코딩, config는 정책만 — 재배열/부분실행에 코드 수정 필요 |
| **F3** | 🟠 Med-High | 권한 계약이 집행되지 않음 | "누가 strategy status / recommendation bucket을 쓰는가"가 문서·사후감사로만 존재, 코드가 막지 않음 |
| **F4** | 🟠 Med-High | 트리플-라이트 단일 진실원 부재 | 동일 아티팩트가 DB·`/tmp`·`static` 3곳에 — sync 지연 시 채널 간 불일치, 읽기 폴백 체인이 모듈마다 다름 |
| **F5** | 🟠 Med | 메타-거버넌스 산출 추적 부재 | 3계층 자기개선 루프의 proposal이 적용/반려되었는지 audit trail 없음 — 오버엔지니어링 의심 |
| **F6** | 🟠 Med | 거대 에이전트 / 변종 분기 | `recommendation_agent.py` 2949줄 + `_after_disclosure` 변종 = 파라미터화 부재의 신호 |
| **F7** | 🟠 Med | 에이전트 클러스터 중복 | scout 계열·validation worker 계열·parameter-evaluator 계열이 60~80% 로직 공유 추정 |
| **F8** | 🟡 Low-Med | config 키 ↔ 파일명 불일치 | `capacity_planner`→`validation_capacity_planner.py` 등 매핑이 암묵적, IDE/유지보수 마찰 |
| **F9** | 🟡 Low-Med | 공유 라이브러리 저활용 | `lib/runtime.py`의 read/write 추상화를 일부 에이전트만 사용, 읽기 패턴 copy-paste |
| **F10** | 🟡 Low-Med | 아티팩트 스키마 미강제 | `agent_contract.v1` 봉투는 있으나 read 시 검증 없음, `.contract.status` vs `.status` 이중 형태 |
| **F11** | 🟡 Low | 리테이닝되지 않은 거대 리포트 dict | `research_pipeline_agent.py:610` 단일 표현식 리포트 dict — 가독성/diff 난이도 |
| **F12** | 🟡 Low | 인벤토리 위생 | 108개 중 ~19개가 메인 파이프라인 밖(toolbox/manual/orphan), 분류 라벨 부재 (`pt_review.py`는 어디서도 미참조) |

심각도 기준: 🔴 데이터 정합성/조직 통제에 직접 영향 · 🟠 유지보수성/확장성 저하 · 🟡 위생/일관성.

## 개선 로드맵 (제안)

> 모든 항목은 제안이며, 적용 전 베이먼/오너 승인 필요. "no real orders" 경계와 기존 포트·볼륨·네트워크 구성은 건드리지 않는다.

### P1 — 데이터 정합성 (가장 먼저, 저위험·고효과)
- **F1/F4**: 아티팩트에 `run_at`/`source_run_id` 신선도 스탬프를 *읽는 쪽에서* 검증. 상류 단계가 이번 사이클에 안 돌았으면 하류가 stale 입력임을 경고·기록(현재는 조용히 통과). 코드 변경 없이 우선 "어떤 단계가 어떤 아티팩트를 소비하는지" 의존 매트릭스부터 문서화.
- **F10**: `agent_contract.v1` 봉투 필수 필드(`agent/status/run_at/...`)를 read 시 1회 검증하는 얇은 게이트 추가 제안(나중 단계).

### P2 — 오케스트레이션 구조 (중위험)
- **F2**: 89개 단계를 `add()` 코드에서 → 선언적 매니페스트(이미 있는 `research_pipeline.yaml` 확장: `order`, `args`, `consumes`, `produces`, `when` 필드)로 이전. 한 번에 전부 말고, **새 단계부터 config-driven**으로 추가하는 점진 전환.
- **F3**: 권한 계약을 코드 게이트로 승격 — 예: strategy status는 `strategy_lifecycle`만 쓰도록 DB write helper에 writer 화이트리스트, recommendation 최종 bucket은 `investment_committee`만. 우선 "현행 writer 실제 매핑" 감사부터.

### P3 — 단순화 / 통합 (점진, 기능 동일 리팩토링)
- **F6/F7**: `recommendation_agent` 공통 로직 추출 후 `_after_disclosure`를 플래그로 흡수. scout 3종·validation worker 4종을 `--mode` 파라미터 단일 에이전트로 통합 검토.
- **F5**: `org_evaluator` + `org_architecture_review` 통합 검토 + guardian의 auto-apply 실행 ledger 추가.
- **F8/F9/F12**: 파일명/키 표준화, `lib/runtime.py` 사용 의무화, toolbox/manual/orphan 라벨링(기존 `docs_current_org_structure_review.md`의 "Toolbox inventory" 제안 계승).
- **별도 리뷰 권장**: 개별 에이전트 *로직 정확성*(look-ahead, 통계 타당성, 엣지케이스) — 이번 아키텍처 리뷰 범위 밖.

### 점검 순서 제안
P1(문서화·신선도 검증) → P2(선언적 오케스트레이션 점진 전환) → P3(통합·정확성). 각 단계는 독립적으로 롤백 가능하도록 작게.

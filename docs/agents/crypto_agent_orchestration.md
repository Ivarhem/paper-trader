# Crypto Strategy Agent Orchestration

이 문서는 `paper_trader`를 코인 전략 실험실로 확장하기 위한 멀티 에이전트 운영 설계다.

목표는 수익 보장이 아니라, 전략 후보를 반복적으로 생성·검증·탈락시키고 paper forward-test 후보만 남기는 것이다.

## 권한 원칙

- 실거래 주문 금지
- 거래소 API key 사용 금지
- public market data + paper simulation만 허용
- 모든 전략 출력은 후보이며 투자 조언/수익 보장이 아니다
- 실거래 전환은 Bayman의 명시 승인 없이는 불가

## 에이전트 역할

### 1. Strategy Designer Agent

역할:
- 전략 가설 생성
- parameter search space 제안
- 시장 regime별 전략 family 제안

출력 예:

```json
{
  "strategy_family": "rsi_reversion",
  "hypothesis": "급락 후 단기 평균회귀를 노린다",
  "symbols": ["KRW-BTC", "KRW-ETH"],
  "timeframes": ["1h", "4h"],
  "parameter_grid": {
    "rsi_buy": [25, 30, 35],
    "rsi_sell": [50, 55, 60]
  },
  "risk_notes": ["강한 하락 추세에서 물릴 수 있음"]
}
```

### 2. Backtest Runner Agent

역할:
- strategy candidate를 API/CLI로 실행
- symbol/timeframe/parameter sweep 수행
- metric 수집

핵심 metric:
- total_return_pct
- buy_hold_return_pct
- max_drawdown_pct
- trade_count
- win_rate_pct
- profit_factor
- fee/slippage assumptions

### 3. Skeptic / Overfit Detector Agent

역할:
- 전략이 왜 가짜 성과일 수 있는지 검토
- 과최적화, 거래횟수 부족, 특정 기간 의존, 수수료 취약성 탐지

탈락 후보:
- trade_count가 너무 적음
- buy&hold 대비 우위가 불명확
- MDD가 과도함
- single symbol/timeframe에서만 성과
- parameter 조금만 바꿔도 성과 붕괴

### 4. Portfolio/Risk Agent

역할:
- position size, max exposure, loss limit 후보 제안
- 여러 전략 동시 운용 시 상관/중복 노출 점검

출력은 주문이 아니라 risk policy 후보여야 한다.

### 5. Forward-Test Agent

역할:
- live/public candle 기준 signal 기록
- paper order만 생성
- backtest 성과와 forward 성과 차이 추적

## 메타 평가 에이전트

### 6. Agent Evaluator Agent

역할:
- 위 에이전트들의 제안 품질을 평가한다.
- 어떤 에이전트가 좋은 후보를 냈는지, 어떤 에이전트가 위험한 제안을 반복하는지 추적한다.

평가 대상:
- Strategy Designer의 후보 품질
- Backtest Runner의 실험 충실도
- Skeptic의 위험 탐지 적중률
- Risk Agent의 보수성/실용성
- Forward-Test Agent의 기록 일관성

평가 metric:

```text
candidate_hit_rate      paper forward-test 후보로 살아남은 비율
false_positive_rate     backtest는 좋았지만 forward에서 무너진 비율
risk_catch_rate         skeptic이 사전에 지적한 리스크가 실제로 발생한 비율
overfit_rejection_rate  과최적화 후보를 얼마나 잘 걸렀는지
decision_latency        후보 생성부터 탈락/승격까지 걸린 시간
traceability_score      가설→실험→결과→결정 연결이 명확한지
```

Agent Evaluator 출력 예:

```json
{
  "cycle_id": "2026-04-30-crypto-lab",
  "agent_scores": {
    "strategy_designer": 0.62,
    "backtest_runner": 0.91,
    "skeptic": 0.78,
    "risk_agent": 0.84,
    "forward_test": 0.70
  },
  "promote": ["rsi_reversion:KRW-BTC:1h"],
  "reject": ["ma_cross:KRW-ETH:1h"],
  "notes": [
    "Strategy Designer가 거래횟수 부족 후보를 자주 냄",
    "Skeptic의 MDD 경고가 유효했음"
  ]
}
```

## Orchestrator Loop

Ray/OpenClaw가 최종 오케스트레이터다.

```text
1. Market data update
2. Strategy Designer 후보 생성
3. Backtest Runner sweep 실행
4. Skeptic/Overfit Detector 검토
5. Portfolio/Risk Agent 검토
6. Agent Evaluator가 에이전트 품질 평가
7. Ray가 후보 승격/탈락 결정
8. Forward-Test Agent가 paper 기록
9. Bayman에게 의미 있는 변경/결정만 보고
```

## Promotion Gate

paper forward-test 후보로 승격하려면 최소 조건을 만족해야 한다.

초기 기본값:

```text
trade_count >= 10
max_drawdown_pct >= -15
profit_factor >= 1.2
total_return_pct > buy_hold_return_pct
여러 parameter 근방에서 성과가 급격히 붕괴하지 않음
```

이 기준은 시장/기간별로 조정 가능하지만, 완화할 때는 이유를 기록해야 한다.

## Next Implementation Steps

1. `/api/backtests/sweep` 추가
2. sweep 결과 저장 테이블 추가
3. `tools/agents/backtest_runner.py` 작성
4. `tools/agents/agent_evaluator.py` 작성
5. cron 기반 daily crypto lab dry-run 구성

## Implemented v1

- `POST /api/backtests/sweep` runs a small parameter sweep for `ma_cross` and `rsi_reversion`.
- Sweep results are persisted in `backtest_runs` for later meta-evaluation.
- `tools/agents/backtest_runner.py` calls the sweep API and emits JSON.
- `tools/agents/agent_evaluator.py` scores sweep candidates against promotion gates and flags rejection reasons.

Example:

```bash
tools/agents/backtest_runner.py --symbols KRW-BTC,KRW-ETH --limit 10 > /tmp/sweep.json
tools/agents/agent_evaluator.py /tmp/sweep.json
```

## External Context Agent

`tools/agents/external_context_agent.py` converts externally gathered headlines/search snippets into a compact context packet for strategy agents.

It must not place trades or generate direct long/short instructions. Its job is risk throttling and context:

```json
{
  "risk_level": "normal|elevated|high",
  "market_regime": "neutral|cautious|risk_off",
  "strategy_adjustments": {
    "allow_new_entries": true,
    "position_size_multiplier": 1.0,
    "prefer_timeframes": ["1h", "4h", "1d"]
  }
}
```

Initial cadence:

- 09:00–23:00 KST, hourly light scan
- Snippet-only search first
- Deep fetch only when risk level changes or important keywords appear
- Notify Bayman only on meaningful risk/regime changes or strategy-gate impact

Example:

```bash
cat /tmp/crypto_headlines.txt | tools/agents/external_context_agent.py
```

### External Context API

Implemented endpoints:

- `POST /api/external-context/snapshots`
- `GET /api/external-context/snapshots?limit=20`
- `GET /api/external-context/latest`

The agent can save directly:

```bash
cat /tmp/crypto_headlines.txt | tools/agents/external_context_agent.py --save
```

## Scheduled Crypto Lab Run

`tools/agents/crypto_lab_run.py` runs the dry-run operating loop:

1. Update Upbit public candles for configured symbols
2. Read latest external context snapshot
3. Run `/api/backtests/sweep`
4. Evaluate candidates with `agent_evaluator.py`
5. Write the latest run packet to `/tmp/crypto_lab_latest.json`

Example:

```bash
tools/agents/crypto_lab_run.py --symbols KRW-BTC,KRW-ETH --timeframe 1h --count 200 --limit 10
```

This remains paper/dry-run only. It does not place real orders and does not use exchange API keys.

## 15m Forward-Test Agent

`tools/agents/forward_test_agent.py` records paper forward signals on the 15m cadence.

It updates public Upbit candles, reads the latest external context, evaluates a conservative MA/RSI signal, and stores the result in `forward_signals`.

It does not create real exchange orders and does not mutate paper positions yet.

Example:

```bash
tools/agents/forward_test_agent.py --symbols KRW-BTC,KRW-ETH --timeframe 15m --count 200
```

## Walk-Forward Strategy Agent

`tools/agents/walk_forward_agent.py` implements cutoff-based out-of-sample validation.

Each cutoff agent uses only data before the cutoff to select a strategy/parameter set, then tests the selected strategy on data after the cutoff. This avoids using future data during strategy selection.

Example:

```bash
tools/agents/walk_forward_agent.py \
  --symbols AAPL,MSFT,NVDA \
  --cutoffs 2024-02-01,2024-03-01 \
  --min-train-bars 20 \
  --min-test-bars 5
```

The same tool can be used for stock or crypto symbols as long as historical bars exist in `price_bars`.

## Stock Historical Research Loop

Crypto real-time loops are paused as PoC. The preferred production-style research loop is stock historical analysis:

- daily or bounded periodic data import
- walk-forward strategy selection with cutoff dates
- out-of-sample validation after each cutoff
- no real-time trading, no auto orders

Implemented tools:

```bash
tools/agents/import_stooq_daily.py --symbols AAPL,MSFT,NVDA,SPY,QQQ --start 2018-01-01
tools/agents/stock_research_run.py --symbols AAPL,MSFT,NVDA,SPY,QQQ
```

`stock_research_run.py` is resource-bounded by symbol universe, cutoff count, and fixed strategy grid. It can run repeatedly without high-frequency market monitoring.

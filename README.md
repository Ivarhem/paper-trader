# Paper Trader

Safe paper/historical research web app for stocks. This project uses FastAPI, SQLite, and a minimal vanilla JS frontend. It does not connect to any broker and does not place real trades. The canonical production path is `/paper-trader`.

## Features

- Watchlist management
- CSV-based price imports
- Simple signals: 5/20 moving average crossover and 5-day momentum
- Simulated buy/sell trades
- Portfolio tracking for cash, open positions, total value, unrealized PnL, and realized PnL
- Trade history and portfolio snapshot history
- Crypto strategy-lab mode using Upbit public candles, with no exchange keys and no real orders

## Quick Start

```bash
cp .env.example .env
./start.sh
```

Open `http://127.0.0.1:8000`.

If you prefer manual setup, create a virtualenv, install `requirements.txt`, then run `uvicorn app.main:app --reload`.

## Configuration

Environment variables are optional for local use:

- `APP_NAME`
- `APP_ENV`
- `APP_VERSION`
- `APP_ROOT_PATH` (production prefix: `/paper-trader`)
- `DATABASE_PATH`
- `INITIAL_CASH`

## Authentication

Production uses app-level authentication backed by `auth_users.json` and the `pt_session` cookie. Admin user management is exposed under `/paper-trader/users` for admin accounts. Keep secrets out of commits.

## API

Health and meta:

- `GET /healthz`
- `GET /health`
- `GET /meta`

App API:

- `GET /api/watchlist`
- `POST /api/watchlist`
- `DELETE /api/watchlist/{id}`
- `GET /api/prices/{symbol}`
- `POST /api/prices/import`
- `GET /api/signals`
- `GET /api/signals/{symbol}`
- `POST /api/trades/buy`
- `POST /api/trades/sell`
- `GET /api/trades`
- `GET /api/portfolio`
- `GET /api/portfolio/history`
- `POST /api/portfolio/reset`

## Crypto strategy lab

The app can import public Upbit candles for crypto paper-trading experiments. This uses public market data only. It does not place real orders and does not require exchange API keys.

```bash
curl -X POST http://127.0.0.1:8000/api/crypto/upbit/import \
  -H "Content-Type: application/json" \
  -d '{"symbol":"KRW-BTC","timeframe":"1h","count":200}'
```

Supported timeframes include `15m`, `1h`, `4h`, and `1d`.

### Backtest API

The first backtest engine supports `ma_cross` and `rsi_reversion`. It includes fee/slippage assumptions and reports return, buy-and-hold return, max drawdown, win rate, profit factor, and recent trades.

```bash
curl -X POST http://127.0.0.1:8000/api/backtests/run \
  -H "Content-Type: application/json" \
  -d '{"symbol":"KRW-BTC","strategy":"ma_cross","initial_cash":100000,"fee_bps":5,"slippage_bps":5}'
```

This is an experiment tool, not investment advice or a profitability guarantee.

## Sample Price Import

The repo includes a sample CSV at `sample_data/prices_sample.csv`. The app seeds it automatically on first startup, and you can re-import it manually:

```bash
curl -X POST http://127.0.0.1:8000/api/prices/import \
  -H "Content-Type: application/json" \
  -d '{"csv_path":"sample_data/prices_sample.csv"}'
```

CSV schema:

```text
symbol,date,open,high,low,close,volume
```

## Notes

- Price import uses repository-relative paths when given a relative `csv_path`.
- Frontend API calls use relative URLs so the app can sit behind a reverse proxy.
- Set `APP_ROOT_PATH=/paper-trader` when serving behind Caddy on that prefix.
- `POST /api/portfolio/reset` clears simulated trades, positions, and snapshots, but keeps watchlist and imported price data.


## Runtime
This service is managed on Ubuntu with systemd.

```bash
sudo systemctl status paper-trader.service
sudo systemctl restart paper-trader.service
journalctl -u paper-trader.service -n 100 --no-pager
```

External base path:
- Canonical: `/paper-trader`
- Legacy redirects: `/paper-trade/*`, `/dashboard`, `/monitor`, `/users`, `/login` should redirect into `/paper-trader/...` at the reverse proxy layer.


## Repository

This project is maintained as its own standalone Git repository under:

```bash
/service/services/paper_trader
```

Typical workflow:

```bash
cd /service/services/paper_trader
git status
git add .
git commit -m "your change"
```

## Next development focus

Suggested next product steps:
- improve portfolio UX and empty states
- support additional CSV import flows
- add lightweight tests for trade and signal paths


## Stock Research Monitor

A read-only monitoring page is available at:

```text
/paper-trader/monitor
```

It focuses on the bounded stock historical research loop: latest run summary, promoted walk-forward candidates, walk-forward results, and recent backtest runs.


## Forward-test signals

The 15m forward-test agent records paper signals only. It does not place real orders and does not change paper positions yet.

```bash
tools/agents/forward_test_agent.py --symbols KRW-BTC,KRW-ETH --timeframe 15m --count 200
```

Signals are visible in `/paper-trader/monitor` and via:

```text
GET /api/forward-signals?limit=100
```


## Korean stock symbols

The stock research loop can include Korean equities via yfinance symbols such as:

```text
005930.KS  Samsung Electronics
000660.KS  SK hynix
035420.KS  NAVER
005380.KS  Hyundai Motor
068270.KS  Celltrion
035720.KS  Kakao
051910.KS  LG Chem
```

The root page `/paper-trader/` now opens the Stock Research Monitor. The older trading dashboard remains available at `/paper-trader/dashboard`.


## Korean market metadata and disclosures

The monitor displays Korean tickers with company names for the default research universe.

Scaffold tools:

```bash
tools/agents/import_krx_corp_list.py
tools/agents/opendart_disclosure_agent.py
```

`opendart_disclosure_agent.py` requires `OPENDART_API_KEY` and currently fetches recent disclosure lists only. Disclosure events should be treated as research features, not trading triggers.


To persist recent disclosures:

```bash
tools/agents/opendart_disclosure_agent.py --begin 2026-04-29 --end 2026-04-30 --save
```

Stored disclosures are exposed through:

```text
GET /api/disclosures
GET /api/disclosures/features
```

The monitor uses these as early strategy research features: recent disclosure count, high/medium-risk event count, positive-event count, and latest event per symbol.


## Disclosure-aware walk-forward

The walk-forward agent now reads persisted OpenDART disclosure events with cutoff discipline:

- only disclosures before each cutoff are used for strategy selection/risk gating
- recent high-risk disclosures reject a candidate
- multiple medium-risk disclosures reject a candidate
- positive disclosures are recorded as supporting context, not automatic promotion

This keeps disclosure data as research features while avoiding look-ahead bias.


## Research organization runner

Run the investment-organization style pipeline:

```bash
tools/agents/research_org_run.py --symbols AAPL,MSFT,NVDA,SPY,QQQ,005930.KS,000660.KS,035420.KS
```

Output:

```text
/tmp/stock_research_org_latest.json
GET /api/research/org/latest
```

This orchestrates Data Agent, Disclosure Analyst, Strategy Researcher, Skeptic Agent, Risk Manager, Portfolio Manager, and Investment Committee. It produces research recommendations only; no real orders.


## Universe scout

`tools/agents/universe_scout.py` scans symbols already present in `price_bars` and ranks candidates by momentum, volume surge, 252-day high proximity, and disclosure risk/support.

Use it standalone:

```bash
tools/agents/universe_scout.py --limit 20 --exclude-risk
```

Or as the front of the research organization:

```bash
tools/agents/research_org_run.py --use-scout --scout-limit 12
```


## Universe curator

`tools/agents/universe_curator.py` maintains universe hygiene. It assigns symbols to:

- `active`
- `watch`
- `quarantine`
- `retired`

It checks stale price data, insufficient history, zero volume, and high-risk OpenDART disclosures such as trading halt, delisting risk, capital reduction, bankruptcy/rehabilitation, embezzlement, or breach of trust. Universe Scout excludes `quarantine` and `retired` symbols.


## Paper recommendations

`tools/agents/recommendation_agent.py` generates research-only candidate recommendations and target/stop reference levels from active universe members. These are not orders and not financial advice.

```bash
tools/agents/recommendation_agent.py --limit 10
```


## Recommendation logic audit

`tools/agents/recommendation_auditor.py` validates the recommendation/target logic at historical cutoff dates. It computes entry/target/stop using only data before the cutoff, then judges success/fail/timeout over a future horizon.

The auditor now supports multi-logic comparison (`conservative_range_v1`, `balanced_range_v1`, `aggressive_range_v1`) and monthly cutoffs via `--monthly-from` / `--monthly-step`.

Recommendation audit robustness checks now include benchmark excess return, per-symbol concentration, and yearly period stability.


## Simulation validation worker

`tools/agents/simulation_validation_worker.py` continuously consumes untested simulation combinations across symbols, monthly cutoffs, horizons, and recommendation logics. Results are persisted to `recommendation_validation_results` and exposed through:

```text
GET /api/validation/summary
GET /api/validation/results
```

This is backlog-driven simulation validation, not daily market monitoring.


## Strategy generator

`tools/agents/strategy_generator_agent.py` enumerates strategy candidates. The recommendation auditor now includes baseline range logics plus a generated `range_grid_v1` parameter grid across target caps, stop caps, target multipliers, and score thresholds. The simulation validation worker consumes all generated logic names by default, skips completed combinations, and the lifecycle evaluator promotes/demotes strategies from accumulated results.


## Unified 15-minute research pipeline

The production research loop is driven by:

```bash
scripts/run_research_org_cron.sh
```

It wraps `tools/agents/research_pipeline_agent.py` with a flock lock and writes status to:

```text
/tmp/research_pipeline_status.json
/tmp/research_pipeline_latest.json
logs/research_pipeline_cron.log
```

The loop is historical/paper research only. It must not place real orders.

Core pipeline outputs include:

```text
/tmp/stock_research_latest.json
/tmp/strategy_candidates_latest.json
/tmp/strategy_novelty_pruner_latest.json
/tmp/active_strategy_balancer_latest.json
/tmp/recommendations_latest.json
/tmp/recommendation_audit_latest.json
/tmp/investment_committee_latest.json
/tmp/committee_performance_ledger_latest.json
/tmp/research_org_evaluation_latest.json
/tmp/org_improvement_guardian_latest.json
```

Routine degraded audit quality is surfaced as warnings, not a service failure. Notify an operator only for actual run failures, consecutive skips/failures, active strategy changes, recommendation anomalies, duplicate/overfit issues, UI/API issues, or concrete non-trivial improvements.

## Adaptive Investment Committee

`tools/agents/investment_committee_agent.py` evaluates recommendations with a fixed evaluator pool:

- `upside_hunter`
- `risk_guardian`
- `evidence_skeptic`
- `balanced_allocator`
- `regime_specialist`

The committee stores latest output and cautious weight learning state in:

```text
/tmp/investment_committee_latest.json
/tmp/investment_committee_weights.json
/tmp/investment_committee_history.json
```

Current weight learning is intentionally conservative and based on audit/history proxies until enough future paper outcome data accumulates.

## Committee performance ledger

`tools/agents/committee_performance_ledger_agent.py` connects committee history, recommendation history, and price bars to evaluate future paper outcomes over the recommendation horizon.

Output:

```text
/tmp/committee_performance_ledger_latest.json
```

Rows can remain `pending` until enough future bars exist. This is expected.

## Org Improvement Guardian

`tools/agents/org_improvement_guardian_agent.py` reads organization-evaluator findings and classifies them as:

- `observe`
- `manual_review`
- `approval_required`
- low-risk reversible maintenance auto-apply

Only low-risk reversible maintenance can be auto-applied. Strategy thresholds, evaluator add/remove, pipeline topology changes, external service changes, and destructive changes require explicit approval.

Outputs:

```text
/tmp/org_improvement_guardian_latest.json
/tmp/org_improvement_guardian_history.json
```
## Research agent organization

The scheduled research pipeline is a lightweight deterministic multi-agent system.
See:

- `configs/research_agents.yaml` — role/mission manifest
- `tools/agents/README.md` — implementation conventions
- `tools/agents/research_pipeline_agent.py` — cron orchestrator

Safety boundary: historical/paper research only; no real trading/orders.


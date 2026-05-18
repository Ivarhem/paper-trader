# Recommendation Audit Quality Gate

Paper/historical research only. This gate is a conservative research-quality screen; it does not approve real trades or orders.

## Purpose

The audit quality gate is designed to prevent weak backtest signals from being presented as strong research evidence. It favors:

- sufficient sample size
- positive average excess return versus benchmark
- limited left-tail losses
- stable performance across periods
- acceptable timeout/fail rates
- diversified symbol coverage
- positive expected excess value after tail/fail/timeout penalties

## Quality score

`quality_score` starts at 100 and subtracts penalties for active flags.

| Flag | Condition | Penalty | Meaning |
|---|---:|---:|---|
| `low_sample_size` | sample count `< 60` | 18 | Not enough audited candidate samples |
| `weak_evaluation_success_confidence_interval` | evaluation success Wilson lower `< 38%` | 18 | Conservative success-rate estimate is weak |
| `weak_execution_success_confidence_interval` | target-hit success Wilson lower `< 25%` | 10 | Strict target/stop success confidence is weak |
| `no_positive_average_excess` | average excess return `<= 0` | 18 | Strategy does not beat benchmark on average |
| `left_tail_excess_risk` | p25 excess return `< -3%` | 14 | Lower quartile outcomes are too negative |
| `period_instability` | positive periods below threshold | 16 | Positive excess is not stable across periods |
| `high_timeout_rate` | timeout rate `> 35%` | 6 | Too many samples do not resolve in horizon |
| `unfavorable_payoff_asymmetry` | average fail drawdown too large vs success upside | 12 | Loss profile is too heavy relative to wins |

Grade mapping:

- `high`: score `>= 80` and no flags
- `medium`: score `>= 60`
- `low`: score `< 60`

## Expected excess value

`expected_excess_value_pct` is a conservative utility proxy:

```text
avg_excess_return_pct
- max(0, -p10_excess_return_pct) * 0.35
- fail_rate_pct * 0.03
- timeout_rate_pct * 0.015
```

If this is `<= 0`, the auditor adds `negative_expected_excess_value` as a hard blocking flag. This flag does not subtract from `quality_score` directly, but it blocks pass verdicts.

## Period stability

Audited candidates are grouped by `cutoff[:4]` year. A period is positive when:

- samples in that period `>= 5`
- average excess return for that period `> 0`

`period_instability` is flagged when:

```text
positive_periods < max(2, tested_periods // 2)
```

This is intentionally conservative, but it is sensitive to year grouping and should be revisited as a future improvement.

## Pass verdict gates

A logic can pass only when all are true:

- samples `>= 60`
- signal rate `>= 5%`
- evaluation success rate `>= 45%`
- average excess return `> 0`
- max symbol concentration `<= 45%`
- stable positive periods `>= 2`
- aggregate quality score `>= 68`
- no hard blocking flags:
  - `negative_expected_excess_value`
  - `period_instability`
  - `symbol_concentration_risk`
  - `no_candidate_buy_signals`
  - `extremely_low_signal_rate`
  - `low_signal_rate`

## Improvement direction

Do not loosen thresholds just to raise scores. Improve in this order:

1. Make the gate explainable with penalty breakdowns.
2. Split market-route quality (KR/US) instead of mixing heterogeneous regimes.
3. Retest exit policy variants that reduce p10/p25/EV drag.
4. Revisit period stability from year grouping to rolling/regime grouping.
5. Separate thresholds for research-watch, paper-eligible, and active promotion.

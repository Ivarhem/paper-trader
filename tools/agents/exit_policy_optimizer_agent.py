#!/usr/bin/env python3
"""Exit Policy Optimizer: proposes paper backtest retests for target/stop/horizon variants when audit shows weak EV or left-tail risk.
"""
from __future__ import annotations
import argparse,json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--output',default='/tmp/exit_policy_optimizer_latest.json'); args=ap.parse_args()
 try: audit=json.loads(Path('/tmp/recommendation_audit_latest.json').read_text(encoding='utf-8'))
 except Exception: audit={}
 best=(audit.get('summary') or {}).get('best') or {}; logic=(audit.get('summary') or {}).get('best_logic')
 flags=best.get('quality_flags') or []
 suggestions=[]
 baseline={k:best.get(k) for k in ['success_rate_pct','avg_excess_return_pct','excess_win_rate_pct','p10_excess_return_pct','p25_excess_return_pct','expected_excess_value_pct','timeout_rate_pct','fail_rate_pct','avg_fail_drawdown_pct','avg_success_upside_pct']}
 baseline_ev=float(best.get('expected_excess_value_pct') or 0)
 baseline_p10=float(best.get('p10_excess_return_pct') or 0)
 baseline_p25=float(best.get('p25_excess_return_pct') or 0)
 baseline_fail=float(best.get('fail_rate_pct') or 0)
 baseline_timeout=float(best.get('timeout_rate_pct') or 0)
 if 'left_tail_excess_risk' in flags or (best.get('p10_excess_return_pct') or 0) < -10:
  suggestions += [
   {'policy':'tighter_stop_grid','target_pct':8,'stop_pct':5,'horizon_days':20,'priority':'high','reason':'left-tail risk 완화 후보'},
   {'policy':'fast_loss_cut','target_pct':7,'stop_pct':4.5,'horizon_days':20,'priority':'high','reason':'p10/p25 개선 목적의 빠른 손절 후보'},
   {'policy':'time_stop_shorter','target_pct':8,'stop_pct':6,'horizon_days':10,'priority':'medium','reason':'timeout/fail drawdown 축소 후보'},
  ]
 if 'negative_expected_excess_value' in flags or (best.get('expected_excess_value_pct') or 0) < 0:
  suggestions += [
   {'policy':'lower_target_with_tighter_stop','target_pct':6,'stop_pct':4,'horizon_days':20,'priority':'high','reason':'기대값 음수 완화 후보'},
   {'policy':'partial_take_profit_proxy','target_pct':5,'stop_pct':5,'horizon_days':20,'partial_take_profit_pct':0.5,'priority':'medium','reason':'승률/EV 개선용 부분익절 proxy'},
   {'policy':'trailing_stop_proxy','target_pct':10,'stop_pct':5,'horizon_days':40,'priority':'medium','reason':'상승 포착 유지 + 손실 제한 후보'},
  ]
 if (best.get('timeout_rate_pct') or 0) >= 20:
  suggestions.append({'policy':'timeout_decay_exit','target_pct':7,'stop_pct':5,'horizon_days':12,'priority':'medium','reason':'timeout 비중이 높아 보유기간 단축 재검증'})
 # Deduplicate by policy and keep high-priority variants first.
 def score_variant(x):
  score={'high':30,'medium':15,'low':5}.get(x.get('priority'),10)
  # Prefer variants that directly address the observed drag: negative EV,
  # severe p10/p25, high fail rate, and timeout decay. These are planning
  # scores only; they do not approve or apply any policy.
  if baseline_ev < 0 and x.get('stop_pct',99) <= 5: score += min(18, abs(baseline_ev)*2)
  if baseline_p10 < -8 and x.get('stop_pct',99) <= 5: score += min(18, abs(baseline_p10) - 6)
  if baseline_p25 < -4 and x.get('target_pct',99) <= 7: score += 8
  if baseline_fail >= 35 and x.get('policy') in ('fast_loss_cut','lower_target_with_tighter_stop'): score += 10
  if baseline_timeout >= 20 and x.get('horizon_days',99) <= 12: score += 10
  rr=(float(x.get('target_pct') or 0) / max(0.1, float(x.get('stop_pct') or 0)))
  if rr < 1.2: score -= 6
  return round(score,2)
 seen=set(); dedup=[]
 for x in sorted(suggestions,key=lambda r: ({'high':0,'medium':1,'low':2}.get(r.get('priority'),1), -score_variant(r))):
  if x['policy'] in seen: continue
  seen.add(x['policy'])
  x=dict(x)
  x['planning_score']=score_variant(x)
  x['validation_success_criteria']={'expected_excess_value_pct':'>= 0','p10_excess_return_pct':'> -8','p25_excess_return_pct':'> -4','no_quality_flags':['negative_expected_excess_value','left_tail_excess_risk']}
  x['authority']='proposal_only_paper_retest'
  dedup.append(x)
 selected=dedup[:8]
 packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'exit_policy_optimizer','real_trading':False,'authority':'proposal_only_no_order_execution','best_logic':logic,'audit_flags':flags,'baseline':baseline,'suggestions':selected,'summary':{'suggestion_count':len(selected),'proposal_count':len(selected),'high_priority_count':sum(1 for x in selected if x.get('priority')=='high'),'needs_exit_retest':bool(selected),'top_policy':selected[0].get('policy') if selected else None,'top_planning_score':selected[0].get('planning_score') if selected else None}}
 attach_contract(packet,'exit_policy_optimizer',status='ok',outputs=packet['summary'],metrics=packet['summary'],warnings=[],next_actions=['Run bounded exit-policy variant validation; do not apply exit params until results improve EV/tail metrics.'] if dedup else [])
 Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()

#!/usr/bin/env python3
"""Strategy Tail Risk Filter: tiers active paper strategies as quality_active, research_active, or tail_risk_limited. Historical/paper research only.
"""
from __future__ import annotations
import argparse,json,sqlite3,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db, utc_now, list_strategy_registry
from tools.agents.lib.agent_contract import attach_contract

def j(s):
 try: return json.loads(s or '{}')
 except Exception: return {}
def risk_grade(row):
 s=row.get('summary') or {}; flags=[]; score=100
 p10=float(s.get('p10_excess_return_pct') or 0); p25=float(s.get('p25_excess_return_pct') or 0); ev=float(s.get('expected_excess_value_pct') or 0); exwin=float(s.get('excess_win_rate_pct') or 0); q=float(s.get('aggregate_quality_score') or 0)
 if ev < -3: flags.append('negative_expected_excess_value'); score-=30
 elif ev < 0: flags.append('weak_expected_excess_value'); score-=15
 if p10 < -12: flags.append('left_tail_excess_risk'); score-=25
 elif p10 < -8: flags.append('moderate_left_tail_risk'); score-=10
 if p25 < -5: flags.append('weak_p25_excess'); score-=12
 if exwin < 50: flags.append('weak_excess_win_rate'); score-=12
 if q < 50: flags.append('low_aggregate_quality'); score-=12
 grade='quality_active' if score>=72 and ev>=0 and p10>=-8 and exwin>=52 else ('research_active' if score>=45 else 'tail_risk_limited')
 return grade, max(0,score), flags

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--apply',action='store_true'); ap.add_argument('--demote-severe',action='store_true',help='Demote severe tail-risk active strategies to probation for paper research safety'); ap.add_argument('--min-research-active',type=int,default=3,help='Keep at least this many paper research-active strategies unless all are severely unsafe'); ap.add_argument('--output',default='/tmp/strategy_tail_risk_filter_latest.json'); ap.add_argument('--apply-status',action='store_true',help='Apply severe status demotions. Default keeps status authority with strategy_lifecycle and emits proposals only.'); args=ap.parse_args(); init_db()
 rows=list_strategy_registry(); active=[r for r in rows if r.get('status')=='active']; assessments=[]; updates=[]
 remaining_active=len(active)
 conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
 for r in active:
  grade,score,flags=risk_grade(r); summ=r.get('summary') or {}; old=summ.get('active_tier') or summ.get('quality_tier')
  summ['quality_tier']=grade; summ['tail_risk_score']=round(score,2); summ['tail_risk_flags']=flags; summ['quality_active']=grade=='quality_active'; summ['research_active']=grade!='quality_active'
  assessments.append({'logic':r['logic'],'status':r['status'],'quality_tier':grade,'tail_risk_score':round(score,2),'flags':flags,'expected_excess_value_pct':summ.get('expected_excess_value_pct'),'p10_excess_return_pct':summ.get('p10_excess_return_pct'),'excess_win_rate_pct':summ.get('excess_win_rate_pct')})
  if args.apply:
   reason=r.get('reason') or ''
   new_status=r['status']
   demoted=False
   if grade!='quality_active' and 'tail-risk-limited' not in reason:
    reason=(reason+'; tail-risk-limited research active').strip('; ')
   demotion_proposed = args.demote_severe and grade == 'tail_risk_limited' and score < 25 and remaining_active > args.min_research_active
   if demotion_proposed:
    new_status='probation'; demoted=True
    reason=(reason+'; tail_risk_filter proposes probation for severe tail risk').strip('; ')
   if demoted and args.apply_status:
    remaining_active-=1
   status_to_write = new_status if (demoted and args.apply_status) else r['status']
   conn.execute('UPDATE strategy_registry SET status=?, summary_json=?, reason=?, updated_at=? WHERE logic=?',(status_to_write,json.dumps(summ,ensure_ascii=False,sort_keys=True),reason,utc_now(),r['logic']))
   if demoted and args.apply_status:
    conn.execute('INSERT INTO strategy_state_events (logic,old_status,new_status,reason,event_json,created_at) VALUES (?,?,?,?,?,?)',(r['logic'],r['status'],new_status,reason,json.dumps({'agent':'strategy_tail_risk_filter','tail_risk_score':score,'flags':flags,'authority':'legacy_apply'},ensure_ascii=False),utc_now()))
   if old != grade or demoted: updates.append({'logic':r['logic'],'old_tier':old,'new_tier':grade,'old_status':r['status'],'proposed_status':new_status if demoted else r['status'],'new_status':status_to_write,'demoted':demoted and args.apply_status,'demotion_proposed':demoted,'authority':'proposal_only' if demoted and not args.apply_status else 'applied' if demoted else 'tier_only','canonical_writer':'strategy_lifecycle','flags':flags})
 conn.commit(); conn.close()
 packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'strategy_tail_risk_filter','real_trading':False,'applied':args.apply,'demote_severe':args.demote_severe,'min_research_active':args.min_research_active,'items':assessments,'updates':updates,'summary':{'active_count':len(active),'quality_active_count':sum(1 for x in assessments if x['quality_tier']=='quality_active'),'research_active_count':sum(1 for x in assessments if x['quality_tier']=='research_active'),'tail_limited_count':sum(1 for x in assessments if x['quality_tier']=='tail_risk_limited')}}
 attach_contract(packet,'strategy_tail_risk_filter',status='ok',outputs=packet['summary'],metrics=packet['summary'],warnings=[],next_actions=['Use quality_tier to weight recommendation scoring and active promotion.'])
 Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()

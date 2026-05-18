#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sqlite3, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.config import get_settings
from app.database import init_db, list_universe_members, list_strategy_registry
from app.symbols import display_name, resolve_symbol
from tools.agents.lib.agent_contract import attach_contract
from tools.agents.recommendation_agent import recommend
from tools.agents.lib.corporate_actions import symbol_corporate_action_risk


def market(symbol): return 'KR' if symbol.endswith(('.KS','.KQ')) else 'US'

def pct(a,b): return round((a/b-1)*100,2) if b else None

def rows_for(conn,symbol):
    return conn.execute("SELECT date, close, volume FROM price_bars WHERE symbol=? AND timeframe='1d' ORDER BY date ASC",(symbol,)).fetchall()

def ret(rows,days):
    if len(rows)<=days: return None
    return pct(float(rows[-1]['close']), float(rows[-days-1]['close']))

def import_if_missing(symbol,start):
    p=subprocess.run([sys.executable,'tools/agents/import_stooq_daily.py','--symbols',symbol,'--start',start],cwd=ROOT,text=True,capture_output=True)
    return {'returncode':p.returncode,'stdout_tail':p.stdout[-2000:],'stderr_tail':p.stderr[-2000:]}

def _extract_bounded_research_symbol(parsed: dict, symbol: str) -> tuple[dict | None, dict | None]:
    symbol_summary=None
    symbol_result=None
    if isinstance(parsed, dict):
        for row in ((parsed.get('summary') or {}).get('symbol_summaries') or []):
            if isinstance(row, dict) and row.get('symbol') == symbol:
                symbol_summary=row
                break
        wf=parsed.get('walk_forward') or []
        if isinstance(wf, dict):
            wf=wf.get('results') or []
        for row in wf:
            if isinstance(row, dict) and row.get('symbol') == symbol:
                symbol_result=row
                break
    return symbol_summary, symbol_result


def run_bounded_research(symbol,runs,start):
    cutoffs=['2023-01-01','2024-01-01','2025-01-01','2026-01-01'][:max(1,min(runs,4))]
    cached=None
    try:
        cached=json.loads(Path('/tmp/stock_research_latest.json').read_text(encoding='utf-8'))
        cached_summary, cached_result = _extract_bounded_research_symbol(cached, symbol)
        if cached_summary:
            return {'returncode':0,'cutoffs':cutoffs,'parsed':cached,'symbol_summary':cached_summary,'symbol_result':cached_result,'cached':True,'stdout_tail':'','stderr_tail':''}
    except Exception:
        cached=None
    p=subprocess.run([sys.executable,'tools/agents/stock_research_run.py','--symbols',symbol,'--cutoffs',','.join(cutoffs),'--start',start],cwd=ROOT,text=True,capture_output=True,timeout=240)
    parsed=None
    try:
        parsed=json.loads(Path('/tmp/stock_research_latest.json').read_text(encoding='utf-8'))
    except Exception:
        try:
            parsed=json.loads(p.stdout[p.stdout.find('{'):])
        except Exception:
            parsed=None
    symbol_summary, symbol_result = _extract_bounded_research_symbol(parsed, symbol) if isinstance(parsed, dict) else (None, None)
    return {'returncode':p.returncode,'cutoffs':cutoffs,'parsed':parsed,'symbol_summary':symbol_summary,'symbol_result':symbol_result,'cached':False,'stdout_tail':p.stdout[-2000:],'stderr_tail':p.stderr[-2000:]}


def decision_from_review(active_evaluation, validation, trend, corporate_action_risk, recommendation_hint):
    if corporate_action_risk.get('flagged'):
        return {
            'verdict': 'avoid', 'label': '매수 금지', 'grade': 'danger', 'buy_opinion': False,
            'confidence': 'high',
            'reason': '감자/거래정지 등 기업행위 리스크가 감지되어 이벤트 해소 전까지 매수 검토 대상에서 제외합니다.',
            'checklist': ['기업행위/거래정지 공시 해소 확인', '변경상장/조정주가 반영 후 재검토'],
        }
    if active_evaluation:
        action = active_evaluation.get('action')
        score = float(active_evaluation.get('score') or 0)
        label = active_evaluation.get('action_label') or action or '-'
        if action == 'candidate_buy_zone':
            if score >= 70:
                verdict, grade, conf = 'buy_candidate', 'good', 'medium'
                title = '매수 후보'
            elif score >= 55:
                verdict, grade, conf = 'weak_buy_candidate', 'caution', 'low'
                title = '약한 매수 후보'
            else:
                verdict, grade, conf = 'watch', 'neutral', 'low'
                title = '관망 우선'
            return {
                'verdict': verdict, 'label': title, 'grade': grade, 'buy_opinion': verdict in ('buy_candidate','weak_buy_candidate'),
                'confidence': conf,
                'reason': f"현재 active 전략이 '{label}'로 평가했고 점수는 {round(score,2)}입니다.",
                'checklist': ['추천 사유/리스크 노트 확인', '목표가 대비 손절 기준 보상-위험 확인', '최신 공시/거래정지 여부 확인'],
            }
        if action == 'watch':
            return {'verdict':'watch','label':'관망','grade':'neutral','buy_opinion':False,'confidence':'medium','reason':f"현재 active 전략 판단이 '{label}'입니다. 바로 매수 의견은 아닙니다.",'checklist':['추가 신호 또는 점수 개선 확인','가격/거래량 추세 재확인']}
        return {'verdict':'avoid','label':'매수 부적합','grade':'danger','buy_opinion':False,'confidence':'medium','reason':f"현재 active 전략 판단이 '{label}'입니다.",'checklist':['리스크 해소 전 신규 매수 보류']}
    avg = validation.get('avg_excess_return_pct')
    sr = validation.get('success_rate_pct') or 0
    samples = validation.get('samples') or 0
    r20 = trend.get('r20_pct')
    if recommendation_hint == 'historically_supported_watch_candidate':
        return {'verdict':'research_watch','label':'관심 관찰 후보','grade':'neutral','buy_opinion':False,'confidence':'low','reason':f"현재 active 매수 신호는 없지만 과거 검증 {samples}건에서 평균 초과수익 {avg}% / 성공률 {round(sr,2)}%가 확인됩니다.",'checklist':['active 전략 신호 발생 여부 대기','관심종목 편입 후 지속 모니터링']}
    if avg is not None and avg < 0:
        return {'verdict':'weak_edge','label':'매수 근거 약함','grade':'danger','buy_opinion':False,'confidence':'medium','reason':f"과거 검증 평균 초과수익이 {avg}%로 약합니다.",'checklist':['검증 성과 개선 전 매수 보류']}
    return {'verdict':'insufficient_signal','label':'매수의견 없음','grade':'neutral','buy_opinion':False,'confidence':'low','reason':f"현재 active 매수 신호가 없고, 검토 정보는 참고용입니다. 20일 추세 {r20}% / 검증 샘플 {samples}건.",'checklist':['active 전략 신호 확인','검증 샘플/공시/추세 추가 확인']}

def main():
    ap=argparse.ArgumentParser(description='On-demand symbol review from universe/seed or bounded fresh analysis')
    ap.add_argument('--symbol', required=True)
    ap.add_argument('--runs', type=int, default=3)
    ap.add_argument('--start', default='2019-01-01')
    ap.add_argument('--output', default='/tmp/symbol_review_latest.json')
    args=ap.parse_args(); init_db(); resolved=resolve_symbol(args.symbol); symbol=resolved["symbol"]
    universe=list_universe_members(limit=1000)
    member=next((x for x in universe if x['symbol'].upper()==symbol), None)
    conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
    rows=rows_for(conn,symbol)
    imports=None; fresh_research=None
    if not rows:
        imports=import_if_missing(symbol,args.start)
        rows=rows_for(conn,symbol)
    latest=float(rows[-1]['close']) if rows else None
    strategy_rows=list_strategy_registry()
    strict_active=[x for x in strategy_rows if x.get('status')=='active']
    repair_active=[x for x in strategy_rows if x.get('status') in ('repair_active','validation_active')]
    active=strict_active or repair_active
    repair_strategy_mode=bool(repair_active and not strict_active)
    corporate_action_risk=symbol_corporate_action_risk(conn, symbol)
    active_evaluation=recommend(conn, symbol, active)
    # Only run bounded historical research when we cannot produce a reasonable current active-strategy evaluation.
    if not member and rows and not active_evaluation:
        fresh_research=run_bounded_research(symbol,args.runs,args.start)
    val_rows=conn.execute("SELECT logic,action,result,cutoff,horizon_days,final_return_pct,excess_return_pct FROM recommendation_validation_results WHERE symbol=? ORDER BY cutoff DESC,id DESC LIMIT 200",(symbol,)).fetchall()
    conn.close()
    val=[dict(x) for x in val_rows]
    if not member and rows and not val and fresh_research is None:
        # On-demand review should produce real historical context for newly inspected symbols.
        # Current active/repair signals alone are not enough for a detailed review.
        conn.close()
        fresh_research=run_bounded_research(symbol,args.runs,args.start)
        conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
        val_rows=conn.execute("SELECT logic,action,result,cutoff,horizon_days,final_return_pct,excess_return_pct FROM recommendation_validation_results WHERE symbol=? ORDER BY cutoff DESC,id DESC LIMIT 200",(symbol,)).fetchall()
        conn.close()
        val=[dict(x) for x in val_rows]
    successes=sum(1 for x in val if x.get('result')=='success')
    excess_vals=[float(x['excess_return_pct']) for x in val if x.get('excess_return_pct') is not None]
    action_counts={}
    for x in val:
        action_counts[x.get('action') or '-'] = action_counts.get(x.get('action') or '-', 0) + 1
    validation={'samples':len(val),'action_counts':action_counts,'success_rate_pct':round(successes/len(val)*100,2) if val else None,'avg_excess_return_pct':round(sum(excess_vals)/len(excess_vals),2) if excess_vals else None,'recent':val[:10]}
    if not val and fresh_research and fresh_research.get('symbol_summary'):
        ss=fresh_research.get('symbol_summary') or {}
        sr=fresh_research.get('symbol_result') or {}
        runs_n=int(ss.get('runs') or len(fresh_research.get('cutoffs') or []) or 1)
        promotes=int(ss.get('promotes') or 0)
        rejects=int(ss.get('rejects') or 0)
        validation={
            'samples': runs_n,
            'action_counts': {'promote': promotes, 'reject': rejects},
            'success_rate_pct': round(promotes / runs_n * 100, 2) if runs_n else None,
            'avg_excess_return_pct': ss.get('avg_oos_excess_pct'),
            'recent': [sr] if sr else [ss],
            'source': 'bounded_fresh_research_walk_forward',
        }
    status='ok' if rows else 'failed'
    warnings=[] if rows else ['no price data after import attempt']
    trend={'r20_pct':ret(rows,20),'r60_pct':ret(rows,60),'r120_pct':ret(rows,120),'last_price':latest,'bars':len(rows)}
    recommendation_hint='active_strategy_current_eval' if active_evaluation else ('seed_universe_summary' if member else 'fresh_data_summary')
    if not active_evaluation and validation['samples'] and validation['avg_excess_return_pct'] is not None:
        if validation['avg_excess_return_pct']>0 and (validation['success_rate_pct'] or 0)>=45:
            recommendation_hint='historically_supported_watch_candidate'
        elif validation['avg_excess_return_pct']<0:
            recommendation_hint='weak_historical_edge'
    
    if corporate_action_risk.get('flagged'):
        recommendation_hint='corporate_action_quarantine'
        warnings.append('corporate action / trading halt disclosure detected')
    decision=decision_from_review(active_evaluation, validation, trend, corporate_action_risk, recommendation_hint)
    packet={'run_at':datetime.now(timezone.utc).isoformat(),'mode':'symbol_review','real_trading':False,'query':args.symbol,'resolved':resolved,'symbol':symbol,'name':display_name(symbol),'market':market(symbol),'in_universe':bool(member),'universe_member':member,'imports':imports,'fresh_research':fresh_research,'active_evaluation':active_evaluation,'corporate_action_risk':corporate_action_risk,'analysis_source':'active_strategy_current_eval' if active_evaluation else ('bounded_fresh_research' if fresh_research else 'stored_history_summary'),'trend':trend,'validation':validation,'active_strategy_count':len(strict_active),'effective_strategy_count':len(active),'repair_strategy_mode':repair_strategy_mode,'recommendation_hint':recommendation_hint,'decision':decision,'summary':f"{display_name(symbol)}({symbol}) review: {decision['label']} · universe={bool(member)}, bars={len(rows)}, samples={validation['samples']}, avg excess={validation['avg_excess_return_pct']}, 20d={trend['r20_pct']}%."}
    attach_contract(packet,'symbol_review_agent',status=status,inputs={'symbol':symbol,'runs':args.runs,'start':args.start},outputs={'query': args.symbol, 'resolved_symbol': symbol, 'symbol':symbol,'in_universe':bool(member),'active_strategy_count':len(strict_active),'effective_strategy_count':len(active),'repair_strategy_mode':repair_strategy_mode,'validation_samples':validation['samples'], 'analysis_source': ('active_strategy_current_eval' if active_evaluation else ('bounded_fresh_research' if fresh_research else 'stored_history_summary'))},metrics={'bars':len(rows),'validation_samples':validation['samples'],'avg_excess_return_pct':validation['avg_excess_return_pct'], 'active_eval_score': (active_evaluation or {}).get('score')},warnings=warnings,next_actions=['Add to universe/research seed if this symbol should be monitored continuously.'] if not member and rows else [])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
    # Return JSON even for no-data reviews so the UI can render a useful failed review card.
if __name__=='__main__': main()

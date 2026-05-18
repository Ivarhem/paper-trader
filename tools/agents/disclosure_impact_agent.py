#!/usr/bin/env python3
"""Disclosure Impact Agent: assesses disclosure body/title impact and prevents benign repeated filings from becoming hard blockers.
"""
from __future__ import annotations
import argparse, json, os, re, sqlite3, sys, urllib.parse, urllib.request, zipfile
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT))
from app.config import load_env_file, get_settings
from app.database import init_db
from tools.agents.lib.agent_contract import attach_contract
load_env_file(ROOT/'.env')
POSITIVE=['자기주식취득','자사주취득','배당','실적(공정공시)','영업(잠정)실적','수주','공급계약','투자확대','시설투자']
NEG_HIGH=['상장폐지','거래정지','감사의견','의견거절','횡령','배임','회생','파산','감자','자본감소','불성실공시']
NEG_MED=['유상증자','전환사채','신주인수권','CB','BW','소송','담보','질권','반대매매','대량매도','처분결정','자기주식처분']
BENIGN=['최대주주등소유주식변동신고서','임원ㆍ주요주주특정증권등소유상황보고서','주식등의대량보유상황보고서','특수관계인과의내부거래','특수관계인에대한출자','기업설명회','현금ㆍ현물배당']
def now(): return datetime.now(timezone.utc).isoformat()
def ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS disclosure_impact_assessments (
      rcept_no TEXT PRIMARY KEY, symbol TEXT, assessed_at TEXT NOT NULL,
      impact_direction TEXT NOT NULL, severity TEXT NOT NULL, confidence REAL NOT NULL,
      reason TEXT, material_fields_json TEXT NOT NULL, document_excerpt TEXT, payload_json TEXT NOT NULL)""")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_disclosure_impact_symbol ON disclosure_impact_assessments(symbol, assessed_at DESC)')
def fetch_doc(api_key, rcept_no):
    if not api_key: return ''
    url='https://opendart.fss.or.kr/api/document.xml?'+urllib.parse.urlencode({'crtfc_key':api_key,'rcept_no':rcept_no})
    try:
        raw=urllib.request.urlopen(url,timeout=25).read(); zp=Path('/tmp/dart_doc_%s.zip'%rcept_no); zp.write_bytes(raw)
        with zipfile.ZipFile(zp) as zf: txt='\n'.join(zf.read(n).decode('utf-8','ignore') for n in zf.namelist()[:3])
        txt=re.sub(r'<[^>]+>',' ',txt); txt=re.sub(r'\s+',' ',txt); return txt[:12000]
    except Exception: return ''
def assess(report, text, risk_level):
    body=(report+' '+(text or '')[:6000]).replace(' ','')
    hits_high=[k for k in NEG_HIGH if k.replace(' ','') in body]; hits_med=[k for k in NEG_MED if k.replace(' ','') in body]; hits_pos=[k for k in POSITIVE if k.replace(' ','') in body]; hits_benign=[k for k in BENIGN if k.replace(' ','') in body]
    direction='neutral'; severity='low'; confidence=0.68; reason='정기/반복성 또는 영향 제한 공시로 평가'
    if hits_high: direction='negative'; severity='high'; confidence=0.86; reason='고위험 공시 키워드/본문 근거: '+', '.join(hits_high[:3])
    elif hits_med: direction='negative'; severity='medium'; confidence=0.78; reason='희석/처분/소송 등 중위험 공시 근거: '+', '.join(hits_med[:3])
    elif hits_pos and not hits_med: direction='positive'; severity='low'; confidence=0.72; reason='긍정/주주환원/실적 관련 공시 근거: '+', '.join(hits_pos[:3])
    elif risk_level=='medium' and hits_benign: direction='neutral'; severity='low'; confidence=0.76; reason='제목상 medium이나 대형주 반복/정정/소유상황 공시로 영향 제한: '+', '.join(hits_benign[:3])
    elif risk_level=='medium': direction='negative'; severity='low'; confidence=0.55; reason='본문 중요 악재 키워드는 약하나 기존 medium 분류 유지'
    return {'impact_direction':direction,'severity':severity,'confidence':confidence,'reason':reason,'material_fields':{'high_hits':hits_high,'medium_hits':hits_med,'positive_hits':hits_pos,'benign_hits':hits_benign,'source':'document+title' if text else 'title_fallback'}}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--limit',type=int,default=80); ap.add_argument('--symbol'); ap.add_argument('--recommendations',default=None,help='Prioritize symbols from recommendations JSON'); ap.add_argument('--output',default=None); ap.add_argument('--fetch-documents',action='store_true')
    args=ap.parse_args();
    if args.output is None:
        args.output = '/tmp/disclosure_impact_latest.json' if not args.symbol else f'/tmp/disclosure_impact_{args.symbol.upper().replace(".","_")}_latest.json'
    init_db(); key=os.getenv('OPENDART_API_KEY')
    conn=sqlite3.connect(get_settings().database_path, timeout=30); conn.row_factory=sqlite3.Row; conn.execute('PRAGMA busy_timeout=30000'); ensure(conn)
    symbols=[]
    if args.symbol:
        symbols=[args.symbol.upper()]
    if args.recommendations:
        try:
            rec=json.loads(Path(args.recommendations).read_text(encoding='utf-8'))
            symbols.extend([str(x.get('symbol')).upper() for x in (rec.get('items') or []) if x.get('symbol')])
        except Exception:
            pass
    symbols=sorted(set([s for s in symbols if s and s!='NONE']))
    if symbols:
        marks=','.join('?' for _ in symbols)
        rows=conn.execute(f'SELECT * FROM disclosure_events WHERE symbol IN ({marks}) ORDER BY rcept_dt DESC,id DESC LIMIT ?', [*symbols,args.limit]).fetchall()
    else:
        where='WHERE symbol IS NOT NULL'; params=[]
        rows=conn.execute(f'SELECT * FROM disclosure_events {where} ORDER BY rcept_dt DESC,id DESC LIMIT ?', [*params,args.limit]).fetchall()
    items=[]
    for r in rows:
        text=fetch_doc(key,r['rcept_no']) if args.fetch_documents else ''; a=assess(r['report_nm'] or '', text, r['risk_level'])
        payload={**a,'rcept_no':r['rcept_no'],'rcept_dt':r['rcept_dt'],'symbol':r['symbol'],'corp_name':r['corp_name'],'report_nm':r['report_nm'],'original_risk_level':r['risk_level']}
        conn.execute("""INSERT OR REPLACE INTO disclosure_impact_assessments (rcept_no,symbol,assessed_at,impact_direction,severity,confidence,reason,material_fields_json,document_excerpt,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",(r['rcept_no'],r['symbol'],now(),a['impact_direction'],a['severity'],a['confidence'],a['reason'],json.dumps(a['material_fields'],ensure_ascii=False),text[:1000] if text else None,json.dumps(payload,ensure_ascii=False,sort_keys=True)))
        items.append(payload)
    conn.commit(); conn.close()
    summary={'assessed':len(items),'negative_high':sum(1 for x in items if x['impact_direction']=='negative' and x['severity']=='high'),'negative_medium':sum(1 for x in items if x['impact_direction']=='negative' and x['severity']=='medium'),'positive':sum(1 for x in items if x['impact_direction']=='positive'),'neutral':sum(1 for x in items if x['impact_direction']=='neutral')}
    packet={'run_at':now(),'mode':'disclosure_impact_assessment','real_trading':False,'summary':summary,'items':items}
    attach_contract(packet,'disclosure_impact_agent',status='ok',inputs={'limit':args.limit,'symbol':args.symbol,'recommendations':args.recommendations,'fetch_documents':args.fetch_documents},outputs=summary,metrics=summary,warnings=[])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()

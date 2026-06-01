#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re, sys, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.agents.lib.agent_contract import attach_contract

def utc_now(): return datetime.now(timezone.utc).isoformat()
def strip_html(s): return re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',s or '')).strip()
def google_news_search(query, max_results=3, timeout=6):
    url='https://news.google.com/rss/search?q='+urllib.parse.quote(query)+'&hl=en-US&gl=US&ceid=US:en'
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        xml=r.read(500000).decode('utf-8','ignore')
    out=[]
    for item in re.findall(r'<item>(.*?)</item>',xml,re.S):
        title=strip_html(re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>',item,re.S).group(1) if re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>',item,re.S) and re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>',item,re.S).group(1) else (re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>',item,re.S).group(2) if re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>',item,re.S) else ''))
        linkm=re.search(r'<link>(.*?)</link>',item,re.S)
        if title:
            out.append({'title':title,'url':linkm.group(1).strip() if linkm else url,'source':'google_news_rss'})
        if len(out)>=max_results: break
    return out

def ddg_search(query, max_results=3, timeout=6):
    url='https://duckduckgo.com/html/?q='+urllib.parse.quote(query)
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        html=r.read(500000).decode('utf-8','ignore')
    out=[]
    for m in re.finditer(r'<a rel="nofollow" class="result__a" href="([^"]+)">(.*?)</a>',html,re.S):
        href=m.group(1); title=strip_html(m.group(2))
        href=urllib.parse.unquote(re.sub(r'^.*uddg=','',href).split('&')[0]) if 'uddg=' in href else href
        if title: out.append({'title':title,'url':href})
        if len(out)>=max_results: break
    return out

def heuristic(issue):
    label=issue.get('label') or issue.get('theme_hint') or '시장 이슈'
    hint=str(issue.get('theme_hint') or label)
    if '증권' in hint or '금융' in hint:
        cause='증시 상승 기대, 거래대금 증가 기대, 브로커리지/IB 수익 개선 기대가 함께 반영됐을 가능성'
    elif '전자' in hint or '반도체' in hint:
        cause='글로벌 반도체/AI 사이클, 원화/수출 민감도, 국내 대형 기술주 수급 개선 기대가 반영됐을 가능성'
    elif '전기' in hint or '전력' in hint:
        cause='AI 인프라·전력망 투자 기대와 전력기기 수요 기대가 반영됐을 가능성'
    elif '바이오' in hint or '제약' in hint:
        cause='개별 임상/허가/수급 이슈 또는 바이오 섹터 순환매 가능성'
    elif '조선' in hint or '중공업' in hint:
        cause='수주/선가/환율 기대와 산업재 순환매 가능성'
    else:
        cause='동일 방향 가격·거래량 움직임이 포착된 모멘텀 클러스터로, 명확한 외부 원인은 추가 뉴스 확인 필요'
    return f"{label}: {cause}. 평균 1D {issue.get('avg_1d_return_pct')}%, 5D {issue.get('avg_5d_return_pct')}%, 거래량 {issue.get('avg_volume_spike')}배."

def synthesize(issue, results):
    base=heuristic(issue)
    titles=' / '.join([r['title'] for r in results[:2]])
    if titles:
        return base + ' 검색 근거 후보: ' + titles
    return base

def main():
    ap=argparse.ArgumentParser(description='Add narrative/source hints to dynamic market issue clusters')
    ap.add_argument('--input',default='/tmp/market_issue_scout_latest.json')
    ap.add_argument('--output',default='/tmp/market_issue_narrative_latest.json')
    ap.add_argument('--no-web',action='store_true')
    args=ap.parse_args()
    scout=json.loads(Path(args.input).read_text(encoding='utf-8')) if Path(args.input).exists() else {'issues':[]}
    enriched=[]; warnings=[]
    for issue in scout.get('issues') or []:
        names=', '.join([m.get('name') or m.get('symbol') for m in (issue.get('members') or [])[:4]])
        if issue.get('market') == 'US':
            q=f"why are {names} stocks moving today market news".strip()
        else:
            q=f"오늘 {issue.get('label')} 급등 이유 {names}".strip()
        results=[]
        if not args.no_web:
            try:
                results=ddg_search(q,3)
            except Exception as exc:
                warnings.append(f"ddg search failed for {issue.get('issue_id')}: {exc}")
            if not results:
                try:
                    results=google_news_search(q,3)
                except Exception as exc:
                    warnings.append(f"google news search failed for {issue.get('issue_id')}: {exc}")
        row=dict(issue)
        row['narrative_query']=q
        row['narrative_sources']=results
        row['narrative']=synthesize(issue,results)
        row['narrative_confidence']=round(min(0.9,(issue.get('confidence') or 0.4)+(0.12 if results else 0)),2)
        row['narrative_status']='web_supported' if results else 'heuristic_only'
        enriched.append(row)
    packet={'run_at':utc_now(),'mode':'market_issue_narrative','real_trading':False,'issues':enriched,'summary':{'issue_count':len(enriched),'web_supported_count':sum(1 for x in enriched if x.get('narrative_status')=='web_supported'),'top_issues':[{'label':x.get('label'),'impact_score':x.get('impact_score'),'narrative_status':x.get('narrative_status'),'narrative':x.get('narrative')} for x in enriched[:5]]}}
    status='ok' if not warnings else 'degraded'
    attach_contract(packet,'market_issue_narrative_agent',status=status,outputs={'issue_count':len(enriched),'web_supported_count':packet['summary']['web_supported_count']},metrics={'issue_count':len(enriched),'warning_count':len(warnings)},warnings=warnings[:10],next_actions=['If heuristic-only narratives dominate, configure a reliable market news source/API.'] if warnings else [])
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8')
    # Also merge narrative back into scout latest for downstream consumers.
    merged=dict(scout); merged['issues']=enriched; merged['narrative_run_at']=packet['run_at']
    Path(args.input).write_text(json.dumps(merged,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()

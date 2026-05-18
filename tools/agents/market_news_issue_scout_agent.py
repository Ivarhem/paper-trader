#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re, sqlite3, sys, urllib.parse, urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.database import get_settings, init_db
from tools.agents.lib.agent_contract import attach_contract

POS=['급등','강세','상승','랠리','신고가','호재','기대감','수혜','상한가','폭등','surge','rally','jumps','soars']
THEME_WORDS=['증권','금융','반도체','전자','AI','전력','전기','조선','방산','원전','바이오','제약','화학','정유','에너지','은행','보험','건설','자동차','로봇','게임','엔터','철강','코스피','코스닥']
BAD_DOMAINS=['blog.naver.com','tistory.com','dcinside','fmkorea','reddit.com']
def utc_now(): return datetime.now(timezone.utc).isoformat()
def strip(s): return re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',s or '')).strip()
def parse_pub_date(text):
 try:
  dt=parsedate_to_datetime(strip(text or ''))
  if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
  return dt.astimezone(timezone.utc).date().isoformat()
 except Exception:
  return None
def title_date(text):
 m=re.search(r'(20\d{2})[.\-/년 ]+(0?[1-9]|1[0-2])[.\-/월 ]+(0?[1-9]|[12]\d|3[01])', text or '')
 if not m: return None
 return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
def days_since(date_s):
 if not date_s: return None
 try: return (datetime.now(timezone.utc).date()-datetime.fromisoformat(date_s[:10]).date()).days
 except Exception: return None
def ddg(q, n=4, timeout=6):
 out=[]
 # Google News RSS is more stable for news-first discovery than generic HTML search.
 for url in [
   'https://news.google.com/rss/search?q='+urllib.parse.quote(q)+'&hl=ko&gl=KR&ceid=KR:ko',
   'https://duckduckgo.com/html/?q='+urllib.parse.quote(q),
 ]:
  try:
   req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
   html=urllib.request.urlopen(req,timeout=timeout).read(800000).decode('utf-8','ignore')
   if 'news.google.com/rss' in url:
    for item in re.findall(r'<item>(.*?)</item>',html,re.S):
     tm=re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>',item,re.S)
     lm=re.search(r'<link>(.*?)</link>',item,re.S)
     pm=re.search(r'<pubDate>(.*?)</pubDate>',item,re.S)
     title=strip((tm.group(1) or tm.group(2)) if tm else '')
     href=strip(lm.group(1) if lm else '')
     published_at=parse_pub_date(pm.group(1) if pm else '') or title_date(title)
     if title: out.append({'query':q,'title':title,'snippet':'','url':href,'domain':urllib.parse.urlparse(href).netloc or 'news.google.com','published_at':published_at,'source_age_days':days_since(published_at),'title_date':title_date(title)})
     if len(out)>=n: return out
   else:
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',html,re.S):
     href=m.group(1); title=strip(m.group(2)); href=urllib.parse.unquote(re.sub(r'^.*uddg=','',href).split('&')[0]) if 'uddg=' in href else href
     published_at=title_date(title)
     if title: out.append({'query':q,'title':title,'snippet':'','url':href,'domain':urllib.parse.urlparse(href).netloc,'published_at':published_at,'source_age_days':days_since(published_at),'title_date':published_at})
     if len(out)>=n: return out
  except Exception:
   continue
 return out
def safe_json(s):
 try: return json.loads(s or '{}')
 except Exception: return {}
def load_universe(conn, limit=900):
 rows=conn.execute("SELECT symbol,payload_json FROM universe_members WHERE status IN ('active','watch','candidate') ORDER BY score DESC LIMIT ?",(limit,)).fetchall(); out=[]
 for r in rows:
  pl=safe_json(r['payload_json']); name=pl.get('name') or r['symbol']; out.append({'symbol':r['symbol'],'name':name})
 return out
def price_confirm(conn, symbols):
 vals=[]
 for s in symbols[:25]:
  rows=conn.execute("SELECT date,close,volume FROM price_bars WHERE symbol=? AND timeframe='1d' ORDER BY date DESC LIMIT 22",(s,)).fetchall()[::-1]
  if len(rows)<2: continue
  ret=(float(rows[-1]['close'])/float(rows[-2]['close'])-1)*100 if rows[-2]['close'] else None
  vols=[float(x['volume'] or 0) for x in rows[:-1]]; vavg=sum(vols)/len(vols) if vols else None
  vals.append({'symbol':s,'return_1d_pct':round(ret,2) if ret is not None else None,'volume_spike':round(float(rows[-1]['volume'] or 0)/vavg,2) if vavg else None})
 rets=[v['return_1d_pct'] for v in vals if v.get('return_1d_pct') is not None]
 vols=[v['volume_spike'] for v in vals if v.get('volume_spike') is not None]
 return {'symbols':vals,'avg_1d_return_pct':round(sum(rets)/len(rets),2) if rets else None,'avg_volume_spike':round(sum(vols)/len(vols),2) if vols else None,'confirmed_count':sum(1 for v in vals if (v.get('return_1d_pct') or 0)>=3)}
def extract_theme(text):
 hits=[w for w in THEME_WORDS if w.lower() in text.lower()]
 if hits: return hits[0]
 m=re.search(r'([가-힣A-Za-z0-9]{2,12})(?:주|관련주)?(?:\s|·)*(?:급등|강세|상승|랠리|수혜)',text)
 return m.group(1) if m else 'market_news'
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--output',default='/tmp/market_news_issue_scout_latest.json'); ap.add_argument('--max-queries',type=int,default=36); args=ap.parse_args(); init_db()
 conn=sqlite3.connect(get_settings().database_path); conn.row_factory=sqlite3.Row
 universe=load_universe(conn); name_to_sym={u['name']:u['symbol'] for u in universe if u.get('name')}
 recs=[]
 try: recs=json.loads(Path('/tmp/recommendations_latest.json').read_text()).get('items',[])
 except Exception: pass
 scout=[]
 try: scout=json.loads(Path('/tmp/market_issue_scout_latest.json').read_text()).get('issues',[])
 except Exception: pass
 queries=['오늘 주식 급등 테마','오늘 특징주 급등 이유','코스피 급등 이유 증권주','코스닥 급등 테마','상한가 이유 오늘','오늘 국내 주식 시장 테마','stocks moving today sector rally','market themes today stocks']
 for i in scout[:8]:
  queries += [f"{i.get('label')} 급등 이유", f"{i.get('label')} 특징주"]
  for m in (i.get('members') or [])[:2]: queries.append(f"{m.get('name') or m.get('symbol')} 급등 이유")
 for r in recs[:12]:
  nm=r.get('name') or r.get('symbol'); queries += [f"{nm} 급등 이유", f"{nm} 특징주 호재"]
 # dedupe
 q=[]
 for x in queries:
  if x and x not in q: q.append(x)
 results=[]; warnings=[]
 for query in q[:args.max_queries]:
  try: results += ddg(query,3)
  except Exception as e: warnings.append(f'search failed: {query}: {e}')
 seen=set(); dedup=[]
 for r in results:
  key=(r['title'],r['domain'])
  if key in seen: continue
  seen.add(key); dedup.append(r)
 clusters=defaultdict(lambda:{'sources':[],'keywords':set(),'mentioned_symbols':set(),'mentioned_names':set()})
 for r in dedup:
  text=(r.get('query','')+' '+r['title']+' '+r.get('snippet',''))
  if not any(p.lower() in text.lower() for p in POS): continue
  theme=extract_theme(text); c=clusters[theme]; c['sources'].append(r)
  for w in THEME_WORDS:
   if w.lower() in text.lower(): c['keywords'].add(w)
  for name,sym in name_to_sym.items():
   if name and len(name)>=2 and name in text:
    c['mentioned_symbols'].add(sym); c['mentioned_names'].add(name)
 issues=[]
 for theme,c in clusters.items():
  src=c['sources']; syms=sorted(c['mentioned_symbols']); pc=price_confirm(conn,syms) if syms else {'symbols':[],'avg_1d_return_pct':None,'avg_volume_spike':None,'confirmed_count':0}
  domain_penalty=sum(1 for s in src if any(b in (s.get('domain') or '') for b in BAD_DOMAINS))*0.04
  conf=0.35+min(len(src),5)*0.08+min(len(syms),5)*0.04+(0.16 if pc.get('confirmed_count') else 0)-domain_penalty
  conf=max(0.1,min(0.92,round(conf,2)))
  source_ages=[s.get('source_age_days') for s in src if s.get('source_age_days') is not None]
  latest_source_date=max([s.get('published_at') for s in src if s.get('published_at')] or [None])
  min_source_age_days=min(source_ages) if source_ages else None
  fresh_source_count=sum(1 for x in source_ages if x <= 3)
  stale_title_count=sum(1 for s in src if s.get('title_date') and days_since(s.get('title_date')) is not None and days_since(s.get('title_date')) > 7)
  risk='high_chase_risk' if (pc.get('avg_1d_return_pct') or 0)>=8 else ('moderate_chase_risk' if (pc.get('avg_1d_return_pct') or 0)>=3 else 'news_only_watch')
  label=f'{theme} 뉴스 이슈' if theme!='market_news' else '시장 뉴스 이슈'
  narrative=' / '.join([s['title'] for s in src[:3]])
  recency_policy='short_term_boost_allowed' if fresh_source_count and stale_title_count == 0 else ('long_term_context_only' if latest_source_date else 'undated_watch_only')
  policy='context_boost_allowed' if recency_policy=='short_term_boost_allowed' and conf>=0.65 and pc.get('confirmed_count') else ('long_term_context_only' if recency_policy=='long_term_context_only' else 'watch_only')
  issues.append({'issue_id':f'news_{theme}_{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")}'.replace(' ','_'),'type':'news_first_market_issue','label':label,'theme_hint':theme,'narrative':narrative,'keywords':sorted(c['keywords']),'source_count':len(src),'sources':src[:6],'mentioned_symbols':syms,'mentioned_names':sorted(c['mentioned_names']),'price_confirmation':pc,'confidence':conf,'risk':risk,'impact_score':round(conf*100,2),'expected_impact':'positive' if conf>=0.55 else 'watch','recommendation_policy':policy,'recency_policy':recency_policy,'latest_source_date':latest_source_date,'min_source_age_days':min_source_age_days,'fresh_source_count':fresh_source_count,'stale_title_count':stale_title_count})
 conn.close(); issues=sorted(issues,key=lambda x:(x['confidence'],x['source_count']),reverse=True)[:12]
 packet={'run_at':utc_now(),'mode':'news_first_market_issue_scout','real_trading':False,'issues':issues,'summary':{'issue_count':len(issues),'top_issues':[{'label':x['label'],'confidence':x['confidence'],'source_count':x['source_count'],'confirmed_count':x['price_confirmation'].get('confirmed_count'),'risk':x['risk']} for x in issues[:6]]}}
 attach_contract(packet,'market_news_issue_scout_agent',status='ok' if not warnings else 'degraded',outputs={'issue_count':len(issues),'top_issues':packet['summary']['top_issues']},metrics={'query_count':len(q[:args.max_queries]),'result_count':len(dedup),'issue_count':len(issues)},warnings=warnings[:8],next_actions=['Fuse news-first issues with price/volume scout; use news-only issues as watch/validation priority unless price confirms.'])
 Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(packet,ensure_ascii=False,indent=2))
if __name__=='__main__': main()

Total output lines: 3372

const rootPath = document.querySelector('meta[name="root-path"]')?.content || "";
const apiBase = rootPath === "/" ? "" : rootPath;
function appUrl(path) { return `${apiBase}${path}`; }
function redirectToLogin() {
  try { sessionStorage.removeItem('pt_basic_auth'); } catch (_) {}
  const current = `${location.pathname.replace(apiBase || '', '')}${location.search || ''}` || '/monitor';
  location.href = `${appUrl('/login')}?next=${encodeURIComponent(current)}`;
}
let symbolNames = {};
const MARKET_STORAGE_KEY = 'paper_trader_selected_recommendation_market';
const UNIVERSE_MARKET_STORAGE_KEY = 'paper_trader_universe_market_filter';
function loadStoredRecommendationMarket() {
  try {
    const value = localStorage.getItem(MARKET_STORAGE_KEY);
    return value === 'US' || value === 'KR' ? value : 'KR';
  } catch (_) {
    return 'KR';
  }
}
function storeRecommendationMarket(value) {
  try {
    if (value === 'US' || value === 'KR') localStorage.setItem(MARKET_STORAGE_KEY, value);
  } catch (_) {}
}
function loadStoredUniverseMarket() {
  try {
    const value = localStorage.getItem(UNIVERSE_MARKET_STORAGE_KEY);
    return value === 'US' || value === 'KR' || value === 'all' ? value : 'KR';
  } catch (_) {
    return 'KR';
  }
}
function storeUniverseMarket(value) {
  try {
    if (value === 'US' || value === 'KR' || value === 'all') localStorage.setItem(UNIVERSE_MARKET_STORAGE_KEY, value);
  } catch (_) {}
}
let selectedRecommendationMarket = loadStoredRecommendationMarket();
let strategyFilterStatus = 'all';
let strategySortKey = 'status';
let strategySearchText = '';
let universeMarketFilter = loadStoredUniverseMarket();
let universeStatusFilter = 'all';
let universeSortKey = 'score';
let universeSearchText = '';
let selectedUniverseSymbol = '';
let recommendationSymbolRanks = new Map();
let recommendationMarketRanks = new Map();
let latestRecommendationBySymbol = new Map();
let auditResultFilter = 'all';
let auditSymbolFilter = '';
let symbolReviewHistoryRows = [];
let symbolReviewPayloadById = new Map();
let expandedSymbolReviewId = null;
const cardLimits = {};
function nameOf(symbol) { return symbolNames[symbol] ? `${symbolNames[symbol]} (${symbol})` : symbol; }
function displayNameOf(row) {
  if (!row) return '-';
  const symbol = row.symbol || '';
  const nm = row.name || symbolNames[symbol];
  return nm && nm !== symbol ? `${nm} (${symbol})` : symbol;
}
function companyNameOf(row) {
  const symbol = row?.symbol || '';
  return row?.name || symbolNames[symbol] || symbol;
}

async function api(path) {
  const urls = apiBase ? [`${apiBase}${path}`, path] : [path];
  let lastError;
  for (const url of urls) {
    try {
      const response = await fetch(url, { credentials: 'same-origin' });
      if (response.status === 401) { redirectToLogin(); throw new Error('로그인이 만료되었습니다. 다시 로그인하세요.'); }
      const text = await response.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; }
      catch (_) {
        lastError = new Error(`서버가 JSON이 아닌 응답을 반환했습니다: ${text.slice(0, 120) || response.statusText}`);
        continue;
      }
      if (response.ok) return data;
      const detail = data?.detail;
      const message = typeof detail === 'string' ? detail : (detail?.error || detail?.stderr || detail?.stdout || JSON.stringify(detail || data));
      lastError = new Error(message || `${response.status} ${response.statusText} ${url}`);
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError;
}

async function staticJson(path, fallback = null) {
  try {
    const response = await fetch(appUrl(`${path}${path.includes('?') ? '&' : '?'}ts=${Date.now()}`), { credentials: 'same-origin' });
    return response.ok ? response.json() : fallback;
  } catch (_) {
    return fallback;
  }
}

async function apiWithStaticFallback(apiPath, staticPath, fallback = null) {
  try {
    return await api(apiPath);
  } catch (_) {
    return staticJson(staticPath, fallback);
  }
}

async function apiPost(path, payload) {
  const response = await fetch(appUrl(path), {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload || {})
  });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) {}
  if (!response.ok) throw new Error(data?.detail || text || response.statusText);
  return data;
}

async function addReviewedSymbolToUniverse(symbol, status = 'watch') {
  const payload = symbolReviewPayloadById.get(String(expandedSymbolReviewId || '')) || {symbol};
  const reason = '상세검토 수동 편입: ' + (payload.summary || payload.recommendation_hint || 'symbol review');
  const body = {
    symbol: payload.symbol || symbol,
    status,
    reason,
    score: payload.active_evaluation?.score ?? null,
    payload: {
      source: 'symbol_review_ui',
      summary: payload.summary,
      decision: payload.decision,
      recommendation_hint: payload.recommendation_hint,
      validation: payload.validation,
      trend: payload.trend
    }
  };
  const result = await apiPost('/api/research/universe-member', body);
  await load().catch(console.error);
  return result;
}


function fmt(value) {
  if (value === null || value === undefined) return "-";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  return String(value);
}

function pct(value) {
  return value === null || value === undefined ? "-" : `${fmt(value)}%`;
}

function badgeClass(value) {
  if (value === 'success' || value === 'pass') return 'badge good';
  if (value === 'fail' || value === 'weak') return 'badge bad';
  if (value === 'timeout' || value === 'watch') return 'badge neutral';
  return 'badge';
}

function activeMonitorTab() {
  return document.querySelector('.monitor-tab.active')?.dataset.tab || (location.hash || '').replace('#', '') || 'overview';
}

function targetMonitorTab(targetId) {
  const target = document.getElementById(targetId);
  return target?.closest('.tab-panel')?.dataset.tabPanel || 'overview';
}

function shouldRenderTarget(targetId) {
  const tab = targetMonitorTab(targetId);
  const active = activeMonitorTab();
  return tab === active || tab === 'overview';
}

function switchMonitorTab(tab) {
  const available = [...document.querySelectorAll('.monitor-tab')].map((btn) => btn.dataset.tab);
  const next = available.includes(tab) ? tab : 'overview';
  document.querySelectorAll('.monitor-tab').forEach((btn) => btn.classList.toggle('active', btn.dataset.tab === next));
  document.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.toggle('active', panel.dataset.tabPanel === next));
  localStorage.setItem('paper_trader_monitor_tab', next);
  if (location.hash !== `#${next}`) history.replaceState(null, '', `#${next}`);
  if (window.paperTraderMonitorLoaded) load().catch(console.error);
}



function setupSymbolSplitResize() {
  const area = document.getElementById('symbol-workarea');
  const resizer = document.getElementById('symbol-split-resizer');
  if (!area || !resizer) return;
  const saved = localStorage.getItem('paper_symbol_history_width');
  if (saved) area.style.setProperty('--symbol-history-width', saved);
  let dragging = false;
  resizer.addEventListener('pointerdown', (event) => {
    dragging = true;
    resizer.setPointerCapture(event.pointerId);
    document.body.classList.add('resizing');
  });
  resizer.addEventListener('pointermove', (event) => {
    if (!dragging) return;
    const rect = area.getBoundingClientRect();
    const width = Math.max(240, Math.min(520, event.clientX - rect.left));
    const value = `${Math.round(width)}px`;
    area.style.setProperty('--symbol-history-width', value);
    localStorage.setItem('paper_symbol_history_width', value);
  });
  const stop = () => { dragging = false; document.body.classList.remove('resizing'); };
  resizer.addEventListener('pointerup', stop);
  resizer.addEventListener('pointercancel', stop);
}

function setupDetailTabs() {
  document.addEventListener('click', (event) => {
    const btn = event.target.closest('.inner-tab');
    if (!btn) return;
    const tab = btn.dataset.detailTab || 'universe';
    document.querySelectorAll('.inner-tab').forEach((el) => el.classList.toggle('active', el.dataset.detailTab === tab));
    document.querySelectorAll('.detail-tab-panel').forEach((el) => el.classList.toggle('active', el.dataset.detailTabPanel === tab));
  });
}

function setupMonitorTabs() {
  const initial = (location.hash || '').replace('#', '') || localStorage.getItem('paper_trader_monitor_tab') || 'overview';
  document.addEventListener('click', (event) => {
    const btn = event.target.closest('.monitor-tab');
    if (!btn) return;
    event.preventDefault();
    switchMonitorTab(btn.dataset.tab || 'overview');
  });
  window.addEventListener('hashchange', () => switchMonitorTab((location.hash || '').replace('#', '') || 'overview'));
  switchMonitorTab(initial);
}

function setHtml(id, html) { const el = document.getElementById(id); if (el) el.innerHTML = html; }

function showMoreCards(targetId, step = 5) {
  cardLimits[targetId] = (cardLimits[targetId] || 5) + step;
  load().catch(alert);
}

function renderCards(targetId, rows, rowRenderer, defaultLimit = 5) {
  const target = document.getElementById(targetId); if (!target) return;
  if (!shouldRenderTarget(targetId)) return;
  if (!rows.length) { target.innerHTML = '<div class="empty-state">No data yet.</div>'; return; }
  const limit = cardLimits[targetId] || defaultLimit;
  const visible = rows.slice(0, limit);
  const more = rows.length > visible.length
    ? `<div class="more-row"><button class="button secondary" onclick="showMoreCards('${targetId}')">더 보기 ${visible.length}/${rows.length}</button></div>`
    : '';
  target.innerHTML = visible.map(rowRenderer).join('') + more;
}
function renderTable(targetId, headers, rows, rowRenderer) {
  const target = document.getElementById(targetId);
  if (!target) return;
  if (!rows.length) {
    target.innerHTML = '<div class="empty-state">No data yet.</div>';
    return;
  }
  target.innerHTML = `<div class="table-wrap"><table class="data-table"><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map(rowRenderer).join("")}</tbody></table></div>`;
}


function marketOf(symbol) {
  return symbol.endsWith('.KS') || symbol.endsWith('.KQ') ? 'KR' : 'US';
}

function recommendationSummaryText(row) {
  const text = row.recommendation_reason || '검증된 active 전략 기준 현재 후보입니다.';
  return text.split('핵심 근거:')[0].replace(/\s+$/,'').replace(/[.]?$/,'.');
}

function humanDecisionSummary(row) {
  const saved = row.human_summary || {};
  const syn = row.investment_committee?.synthesis || {};
  const watch = row.watch_reason || row.critic?.watch_reason || {};
  const cautions = watch.cautions || [];
  const supports = watch.supports || [];
  const blockers = watch.blockers || [];
  const excess = row.validation_basis?.avg_active_excess_return_pct;
  const auditFlags = row.validation_basis?.audit_quality_flags || [];
  const name = displayNameOf(row);
  let headline = saved.headline;
  if (!headline) {
    if (row.action === 'candidate_buy_zone' && syn.decision !== 'reject') headline = `${name}: 매수 후보`;
    else if (row.action === 'avoid' || blockers.length || syn.decision === 'reject') headline = `${name}: 제외/보류`;
    else headline = `${name}: 관찰`;
  }
  const why = saved.why_now || [
    supports[0] ? `긍정 근거는 ${supports[0].label}입니다.` : '',
    excess !== undefined && excess !== null ? `보조 검증 신호의 시장 대비 성과는 후보 유지/보류 판단에만 반영됩니다.` : '',
    auditFlags.length ? `주의 라벨은 ${auditFlags.slice(0,2).map((f)=>auditFlagLabel(f,false)).join(', ')}입니다.` : '조건 라벨 보강 후 재평가가 필요합니다.'
  ].filter(Boolean).join(' ');
  const mainRisk = saved.main_risk || blockers[0]?.label || cautions[0]?.label || (row.risk_notes || [])[0]?.replace('관망 이유: ', '') || '뚜렷한 차단 사유는 제한적입니다.';
  let committeeView = saved.committee_view || '';
  if (!committeeView) {
    if (syn.decision === 'committee_support') committeeView = '위원회는 paper-buy 승인을 지지했습니다.';
    else if (syn.decision === 'research_support') committeeView = '위원회는 연구 후보로는 지지하지만 paper-buy 승인은 보류했습니다.';
    else if (syn.decision === 'watch') committeeView = '위원회는 추가 확인이 필요하다고 봤습니다.';
    else if (syn.decision === 'reject') committeeView = '위원회는 paper-buy 승인을 보류했습니다.';
  }
  const suggested = saved.suggested_action || (row.action === 'candidate_buy_zone'
    ? '목표가와 손절 기준을 먼저 확인한 뒤, 포지션 크기는 검증 강도에 맞춰 보수적으로 정하세요.'
    : '다음 가격 갱신과 추가 검증 결과를 확인하면서 후보군에 남겨두는 정도가 적절합니다.');
  return { headline, why, mainRisk, committeeView, suggested, confidence: saved.confidence_explanation || row.confidence_grade?.description || row.caveat || '' };
}


function summaryBullets(text, limit=5) {
  if (!text) return [];
  return String(text)
    .split(/(?<=\.)\s+/)
    .map((x) => x.trim().replace(/[.]$/, ''))
    .filter(Boolean)
    .slice(0, limit);
}

function hideRawTrustMetricText(items = []) {
  return (items || []).filter((item) => {
    const text = String(item || '');
    return !/(초과승률|검증 샘플|종목 자체 검증 샘플|신뢰 기준값|라벨 주의도)/.test(text);
  });
}


function committeeSplit(row, fallbackText='') {
  const rat = row.committee_rationale || row.investment_committee?.rationale || {};
  let support = rat.support_summary || '';
  let oppose = rat.oppose_summary || '';
  const text = String(fallbackText || '');
  if (!support) {
    const m = text.match(/지지(?:\/관찰)?(?: 근거)?:\s*(.*?)(?:\s*반대(?: 근거)?:|\s*종합:|$)/);
    if (m) support = m[1].trim();
  }
  if (!oppose) {
    const m = text.match(/반대(?: 근거)?:\s*(.*?)(?:\s*종합:|$)/);
    if (m) oppose = m[1].trim();
  }
  return { support, oppose };
}

function committeeCompactText(text) {
  if (!text) return '';
  return String(text)
    .replace(/종합:\s*/g, ' · ')
    .replace(/반대 근거:/g, '반대:')
    .replace(/지지\/관찰 근거:/g, '지지:')
    .trim();
}

function recommendationReasonParts(row) {
  const text = row.recommendation_reason || '';
  const core = text.includes('핵심 근거:') ? text.split('핵심 근거:')[1].split('체크포인트:')[0] : '';
  const checks = text.includes('체크포인트:') ? text.split('체크포인트:')[1] : '';
  const split = (v) => v.split(/[;,]/).map((x) => x.trim()).filter(Boolean).slice(0, 4);
  return { core: split(core), checks: split(checks) };
}

function compactStat(label, value, sub='') {
  return `<div class="rec-stat"><span>${label}</span><b>${value}</b>${sub ? `<em>${sub}</em>` : ''}</div>`;
}

function plainDecisionLine(row, human) {
  const action = row.recommendation_bucket_label || row.action_label || row.action || '관찰';
  const blockers = row.presentation?.primary_blockers || [];
  const score = row.score != null ? `점수 ${fmt(row.score)}` : '점수 확인 중';
  const firstBlocker = readableRiskSummary(blockers[0] || human?.mainRisk || '추가 확인 필요');
  return `${action} · ${score} · 핵심 주의: ${firstBlocker}`;
}

function readableRiskSummary(text) {
  if (!text) return text;
  let out = String(text).trim();
  out = out.replace(/실거래\s*가능\s*전략\s*0개/g, '실거래 승격 없음');
  out = out.replace(/고신뢰\s*과거검증\s*전략\s*없음/g, '고신뢰 과거검증 미확인');
  out = out.replace(/no candidate buy signals/gi, '매수 후보 신호 없음');
  return out;
}

function plainSummaryBullets(row, human) {
  const present = row.presentation || {};
  const positives = present.positive_factors || [];
  const checks = present.next_checks || [];
  const bullets = [];
  if (human?.why) bullets.push(...summaryBullets(human.why, 2).map((x)=>`판단 근거: ${x}`));
  if (positives[0]) bullets.push(`플러스 요인: ${positives[0]}`);
  if (checks[0]) bullets.push(`다음 확인: ${checks[0]}`);
  return bullets.slice(0, 4);
}

function recStatusChips(row) {
  const vb = row.validation_basis || {};
  const chips = [];
  const targetAdj = row.target_return_adjustment;
  chips.push(`<span class="rec-chip ${targetAdj ? 'warn' : 'neutral'}">목표수익률 ${targetAdj?.adjustment_pct_points ?? 0}%p</span>`);
  chips.push(`<span class="rec-chip ${vb.target_adjustment_applied_count ? 'good' : (vb.target_adjustment_count ? 'warn' : 'neutral')}">개선게이트 ${vb.target_adjustment_applied_count || 0}/${vb.target_adjustment_count || 0}</span>`);
  const auditScore = row.presentation?.audit_summary?.min_quality_score ?? vb.audit_quality_min_score;
  if (auditScore !== undefined && auditScore !== null) chips.push(`<span class="rec-chip ${Number(auditScore) >= 60 ? 'good' : 'warn'}">조건 신뢰 ${fmt(auditScore)}</span>`);
  if (row.trade_eligible) chips.push(`<span class="rec-chip good">Paper 후보</span>`);
  return chips.length ? `<div class="rec-chip-row">${chips.join('')}</div>` : '';
}


function readableEvidenceText(text) {
  if (!text) return text;
  let out = String(text);
  out = readableRiskSummary(out);
  out = out.replace(/시장별 전략 평균 초과수익/g, '시장 대비 전략 성과');
  out = out.replace(/상위 신호 평균 초과수익/g, '상위 신호 시장 대비 성과');
  out = out.replace(/audit hard downgrade/g, 'Audit 하드 다운그레이드');
  out = out.replace(/Audit flags:\s*([^/|]+)/g, (_, flags) => {
    const labels = flags.split(',').map((x) => x.trim()).filter(Boolean).map((x) => auditFlagLabel(x, false));
    return `Audit 플래그: ${labels.join(', ')}`;
  });
  Object.keys({
    left_tail_excess_risk:1, negative_expected_excess_value:1, no_positive_average_excess:1,
    period_instability:1, unfavorable_payoff_asymmetry:1, no_candidate_buy_signals:1,
    weak_success_confidence_interval:1, recent_decay:1
  }).forEach((flag) => { out = out.replaceAll(flag, auditFlagLabel(flag, false)); });
  return out;
}

function listBlock(title, items, cls='') {
  const arr = (items || []).filter(Boolean);
  if (!arr.length) return '';
  return `<section class="rec-detail-list ${cls}"><h4>${title}</h4><ul>${arr.slice(0,6).map((x)=>`<li>${readableEvidenceText(x)}</li>`).join('')}</ul></section>`;
}

function evidencePills(items) {
  return `<div class="evidence-pill-row">${(items || []).filter(Boolean).map((x) => `<span class="evidence-pill ${x.kind || 'neutral'}"><b>${x.label}</b>${x.value ?? '-'}</span>`).join('')}</div>`;
}


function committeeDecisionLabel(decision) {
  return {
    committee_support: 'Paper 승인',
    research_support: '연구 지지',
    watch: '관찰',
    reject: '승인 보류',
  }[decision] || '검토 전';
}

function researchDecisionLabel(decision) {
  return {support:'연구 지지', watch:'연구 관찰', ignore:'연구 제외'}[decision] || decision || '-';
}

function riskGateDisplay(row, risk = {}) {
  const vb = row.validation_basis || {};
  const critic = row.critic || {};
  const flags = vb.audit_quality_flags || [];
  const hard = risk.hard_risk || critic.severity === 'high';
  const under = risk.under_validated || critic.issue_type === 'under_validated' || critic.severity === 'under_validated';
  const hasTail = flags.some((f) => ['left_tail_excess_risk','negative_expected_excess_value','no_positive_average_excess','unfavorable_payoff_asymmetry'].includes(f));
  const noEdge = Number(vb.positive_symbol_edge_count || 0) <= 0;
  const lowWin = Number(vb.avg_excess…49701 tokens truncated…>Alpha Agenda</span></div>
      <div class="audit-sub">${orch.mission || 'paper/historical 수익률 개선 병목을 고르고 안전한 연구 액션을 실행합니다.'}</div>
      <div class="strategy-metrics">
        <div><span>정식 Active</span><b>${orch.objective_metrics?.active_count ?? '-'}/${orch.objective_metrics?.target_active ?? activeGap.target_active ?? '-'}</b><small>고신뢰 승격 전략</small></div>
        <div><span>Repair Active</span><b>${orch.objective_metrics?.repair_active_count ?? orch.summary_metrics?.repair_active_count ?? 10}</b><small>watch-only 연구 lane</small></div>
        <div><span>Gap</span><b>${orch.objective_metrics?.active_gap ?? activeGap.gap ?? '-'}</b><small>정식 Active 기준</small></div>
        <div><span>Trade Eligible</span><b>${orch.objective_metrics?.trade_eligible_recommendations ?? '-'}</b><small>paper 승인 후보</small></div>
        <div><span>Assigned Tasks</span><b>${assignedTasks.length}</b><small>개선 과제</small></div>
      </div>
      <div class="audit-foot">${alphaAgenda.map((row) => `<b>${row.priority}/${row.theme}</b>: ${row.objective}<br><span class="hint">${row.action || row.why || ''}</span>`).join('<br>')}${assignedTasks.length ? `<hr><b>Assigned research tasks</b><br>${assignedTasks.map((t) => `<b>${t.priority}/${t.owner_agent}</b>: ${t.target || '-'} · ${t.action}<br><span class="hint">unblock: ${t.unblock_condition || '-'}</span>`).join('<br>')}` : ''}${orch.autonomous_research ? `<hr><b>Autonomous research loop</b>: hypotheses ${orch.autonomous_research.hypothesis_count ?? 0} · plans ${orch.autonomous_research.plan_count ?? 0} · judgments ${orch.autonomous_research.judgment_count ?? 0} · ledger ${orch.autonomous_research.ledger_new_entries ?? 0} · repeats ${orch.autonomous_research.ledger_repeat_count ?? 0} · deduped ${orch.autonomous_research.ledger_deduped_repeat_count ?? 0} · deltas ${orch.autonomous_research.ledger_delta_count ?? 0}<br>${(orch.autonomous_research.top_hypotheses || []).slice(0,3).map((h) => `<b>${h.priority}/${h.experiment_type}</b>: ${h.target}<br><span class="hint">${h.hypothesis || ''}</span>`).join('<br>')}` : ''}${(activeGap.dominant_blockers || []).length ? `<hr><b>Active pool blockers</b>: ${(activeGap.dominant_blockers || []).map((b) => `${b[0]} ${b[1]}`).join(' · ')}` : ''}</div></article>`);
  }



  const supplyWeightEval = pipe.supply_weight_evaluation_summary || {};
  const supplyWeightSummary = supplyWeightEval.summary || {};
  if (supplyWeightEval.run_at || supplyWeightEval.rows_scanned) {
    const proposals = supplyWeightEval.proposals || supplyWeightEval.weight_adjustment_proposals || [];
    const buckets = supplyWeightSummary.by_supply_adjustment_bucket || {};
    setHtml('org-evaluation-cards', (document.getElementById('org-evaluation-cards')?.innerHTML || '') + `
      <article class="audit-card neutral supply-weight-summary"><div class="audit-card-top"><strong>수급/거래주체 가중치 검증</strong><span class="badge neutral">Proposal Only</span></div>
      <div class="audit-sub">${supplyWeightEval.horizon_days ?? '-'}D outcome · rows ${supplyWeightEval.rows_scanned ?? '-'} · proposals ${supplyWeightEval.proposal_count ?? proposals.length ?? 0}</div>
      <div class="audit-foot"><b>가드레일</b>: 표본 기반 제안만 생성, 자동 가중치 변경 없음<br>${Object.entries(buckets).map(([k,v]) => `<b>${k}</b>: n=${v.sample_count ?? 0}, complete=${v.complete_count ?? 0}, gate=${v.gate?.decision || '-'}`).join('<br>') || '아직 평가 요약 없음'}</div></article>`);
  }

  const targetReturnEval = pipe.target_return_adjustment_evaluation_summary || {};
  const targetReturnSummary = targetReturnEval.summary || {};
  if (targetReturnEval.run_at || targetReturnEval.rows_scanned) {
    const arms = targetReturnSummary.arm_sample_backlog || [];
    const currentGroups = targetReturnSummary.by_current_adjustment_pct_points || {};
    const warnings = targetReturnEval.warnings || [];
    setHtml('org-evaluation-cards', (document.getElementById('org-evaluation-cards')?.innerHTML || '') + `
      <article class="audit-card neutral target-return-summary"><div class="audit-card-top"><strong>목표수익률 보정 Arm 검증</strong><span class="badge neutral">Proposal Only</span></div>
      <div class="audit-sub">${targetReturnEval.horizon_days ?? '-'}D outcome · rows ${targetReturnEval.rows_scanned ?? '-'} · proposals ${targetReturnEval.proposal_count ?? 0}${warnings.length ? ` · ${warnings[0]}` : ''}</div>
      <div class="audit-foot"><b>가드레일</b>: 표본 기반 제안만 생성, 자동 목표수익률 보정 변경 없음<br>${arms.map((a) => `<b>${a.adjustment_pct_points}%p</b>: complete=${a.complete_count ?? 0}, need=${a.needed_complete_count ?? 0}, gate=${a.gate || '-'}`).join('<br>') || Object.entries(currentGroups).map(([k,v]) => `<b>${k}%p</b>: n=${v.sample_count ?? 0}, complete=${v.complete_count ?? 0}, gate=${v.gate?.decision || '-'}`).join('<br>') || '아직 arm backlog 없음'}</div></article>`);
  }

  const shockSummary = marketShock?.summary || {};
  const spillSummary = themeSpillover?.summary || {};
  const shockHyps = marketShock?.hypotheses || [];
  const spillThemes = themeSpillover?.themes || themeSpillover?.items || [];
  if ((shockSummary.hypothesis_count || 0) || (spillSummary.theme_count || 0)) {
    setHtml('org-evaluation-cards', (document.getElementById('org-evaluation-cards')?.innerHTML || '') + `
      <article class="audit-card neutral market-shock-summary"><div class="audit-card-top"><strong>장마감 쇼크 / 테마 전파 연구</strong><span class="badge neutral">Hypothesis Only</span></div>
      <div class="audit-sub">급등 ${shockSummary.surge_count ?? 0} · 급락 ${shockSummary.crash_count ?? 0} · 활성 테마 ${shockSummary.active_theme_count ?? 0} · spillover 검증 ${spillSummary.theme_count ?? 0}</div>
      <div class="strategy-metrics">
        <div><span>Movers</span><b>${shockSummary.mover_count ?? '-'}</b></div>
        <div><span>Hypotheses</span><b>${shockSummary.hypothesis_count ?? '-'}</b></div>
        <div><span>Promising</span><b>${spillSummary.promising_count ?? '-'}</b></div>
        <div><span>Watch</span><b>${spillSummary.watch_count ?? '-'}</b></div>
      </div>
      <div class="audit-foot"><b>가드레일</b>: 추천/active 전환 권한 없음 · research_hypothesis → experiment_planner 입력만 허용<br>${shockHyps.slice(0,3).map((h) => `<b>${h.target}</b>: ${h.evidence?.direction || '-'} · score ${fmt(h.evidence?.score)}<br><span class="hint">${h.hypothesis || ''}</span>`).join('<br>')}${spillThemes.length ? `<hr><b>Backtest</b><br>${spillThemes.slice(0,3).map((t) => `<b>${t.theme}</b>: ${t.verdict || t.status || '-'} · excess ${pct(t.avg_excess_return_pct ?? t.mean_excess_return_pct)} · n=${t.event_count ?? t.sample_count ?? '-'}`).join('<br>')}` : ''}</div></article>`);
  }

  renderCards('agent-proposal-cards', evalPayload.agent_proposals || [], (row) => `
    <article class="audit-card agent-proposal"><div class="audit-card-top"><strong>${row.name}</strong><span class="badge neutral">${row.priority}</span></div>
    <div class="audit-sub"><b>트리거</b>: ${row.trigger}<br><b>미션</b>: ${row.mission}</div>
    <div class="audit-foot"><b>v1</b>: ${row.first_version}<br><small>inputs: ${(row.inputs || []).join(', ') || '-'} · outputs: ${(row.outputs || []).join(', ') || '-'}</small></div></article>`);

  renderUniverse(universe.items || []);
  renderSymbolReviewHistory(symbolReviewHistory.items || []);

  renderCards('agent-strategy-cards', agentStrategyDescriptions(pipe.steps || orch.actions || []), (row) => `
    <article class="audit-card agent-strategy"><div class="audit-card-top"><strong>${row.name}</strong><span class="badge neutral">${row.role}</span></div>
    <div class="audit-sub">${row.strategy}</div>
    <div class="audit-foot"><b>가드레일</b>: ${row.guardrail}${row.status ? `<br><small>latest: ${row.status}${row.last_run ? ' · ' + new Date(row.last_run).toLocaleString() : ''}${(row.warnings || []).length ? ' · warnings ' + row.warnings.length : ''}</small>` : ''}</div></article>`);

  renderStrategies(strategies.items || []);

  const promotedFallback = (research.promoted || []).map(promotedToRecommendation);
  const recItems = (recommendations.items && recommendations.items.length) ? recommendations.items : promotedFallback;
  const usingPromotedFallback = !(recommendations.items && recommendations.items.length) && promotedFallback.length > 0;
  const usRecs = recItems.filter((row) => marketOf(row.symbol) === 'US');
  const krRecs = recItems.filter((row) => marketOf(row.symbol) === 'KR');
  const marketSelect = document.getElementById('market-filter');
  if (marketSelect) marketSelect.value = selectedRecommendationMarket;
  const visibleRecs = selectedRecommendationMarket === 'US' ? usRecs : krRecs;
  const marketLabel = selectedRecommendationMarket === 'US' ? '미국' : '한국';
  const benchmarkLabel = selectedRecommendationMarket === 'US' ? 'SPY 기준 시장대비 검증' : 'KOSPI/KRX 종목군 별도 표시';
  const shadowItems = (shadowRecommendations.items || []).map(shadowToResearchWatch);
  const marketShadow = shadowItems.filter((row) => marketOf(row.symbol) === selectedRecommendationMarket);
  const displayRecs = sortRecommendations(visibleRecs.length ? visibleRecs : marketShadow);
  const shadowFallback = !visibleRecs.length && marketShadow.length > 0;
  const recSourceLabel = usingPromotedFallback ? ' · historical promoted 후보 표시' : (shadowFallback ? ' · 보조 연구 관찰 후보 표시' : '');
  const recRunAt = recommendations.run_at || shadowRecommendations.run_at || research.run_at;
  const performanceLabel = marketPerformanceSummary(displayRecs, research);
  setHtml('recommendations-summary', recommendationSummaryNote(marketLabel, recRunAt, benchmarkLabel, recSourceLabel, performanceLabel));
  renderRecommendationSections('recommendations-cards', displayRecs);
  renderPrimaryFundConsensus(recommendations);

  renderTable('universe-curator-table', ['Symbol', 'Status', 'Score', 'Reason'], curator.items || [], (row) => `
    <tr>
      <td>${nameOf(row.symbol)}</td>
      <td>${row.status}</td>
      <td>${fmt(row.score)}</td>
      <td>${row.reason}</td>
    </tr>
  `);

  renderTable('universe-scout-table', ['Symbol', 'Score', '20d', '60d', 'Volume', 'Disclosure', 'Reasons'], scout.selected || [], (row) => `
    <tr>
      <td>${nameOf(row.symbol)}</td>
      <td>${fmt(row.score)}</td>
      <td>${pct(row.return_20d_pct)}</td>
      <td>${pct(row.return_60d_pct)}</td>
      <td>${fmt(row.volume_surge_20v60)}x</td>
      <td>H:${row.disclosures?.high ?? 0} M:${row.disclosures?.medium ?? 0} P:${row.disclosures?.positive ?? 0}</td>
      <td>${(row.reasons || []).join('<br>') || '-'}</td>
    </tr>
  `);

  renderTable('org-roles-table', ['Role', 'Status', 'Summary'], orgRoles, (row) => `
    <tr>
      <td>${row.role}</td>
      <td>${row.status}</td>
      <td>${Object.entries(row).filter(([k]) => !['role','status','results','promoted','rejected','objections','vetoes','watches','imports'].includes(k)).map(([k,v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join('<br>')}</td>
    </tr>
  `);
  const promoted = research.promoted || [];
  const wf = flattenWalkForward(research);
  setHtml('research-metrics', `
    <div class="metric-card"><div class="metric-label">Run At</div><div class="metric-value">${research.run_at ? new Date(research.run_at).toLocaleString() : '-'}</div></div>
    <div class="metric-card"><div class="metric-label">Mode</div><div class="metric-value">${fmt(research.mode || 'none')}</div></div>
    <div class="metric-card"><div class="metric-label">Results</div><div class="metric-value">${wf.length}</div></div>
    <div class="metric-card"><div class="metric-label">Promoted</div><div class="metric-value">${promoted.length}</div></div>
    <div class="metric-card"><div class="metric-label">Symbols</div><div class="metric-value">${(research.walk_forward?.symbols || []).map(nameOf).join(', ') || '-'}</div></div>
    <div class="metric-card"><div class="metric-label">Cutoffs</div><div class="metric-value">${(research.walk_forward?.cutoffs || []).join(', ') || '-'}</div></div>
  `);

  renderTable('promoted-table', ['Symbol', 'Cutoff', 'Strategy', 'Train Return', 'Test Return', 'Test B&H', 'Test MDD'], promoted, (row) => `
    <tr>
      <td>${nameOf(row.symbol)}</td>
      <td>${row.cutoff}</td>
      <td>${row.selected_train?.strategy || '-'}</td>
      <td>${pct(row.selected_train?.total_return_pct)}</td>
      <td>${pct(row.out_of_sample_test?.total_return_pct)}</td>
      <td>${pct(row.out_of_sample_test?.buy_hold_return_pct)}</td>
      <td>${pct(row.out_of_sample_test?.max_drawdown_pct)}</td>
    </tr>
  `);



  const featureRows = Object.entries(disclosureFeatures.features || {}).map(([symbol, feature]) => ({ symbol, ...feature }));
  renderTable('disclosure-feature-table', ['Symbol', '30d Total', 'High', 'Medium', 'Positive', 'Latest'], featureRows, (row) => `
    <tr>
      <td>${nameOf(row.symbol)}</td>
      <td>${row.total}</td>
      <td>${row.high}</td>
      <td>${row.medium}</td>
      <td>${row.positive}</td>
      <td>${row.latest ? `${row.latest.rcept_dt} / ${row.latest.report_nm} (${row.latest.risk_level})` : '-'}</td>
    </tr>
  `);

  renderTable('disclosure-table', ['Date', 'Company', 'Report', 'Risk', 'Category'], disclosures.items || [], (row) => `
    <tr>
      <td>${row.rcept_dt}</td>
      <td>${row.symbol ? nameOf(row.symbol) : row.corp_name}</td>
      <td>${row.report_nm}</td>
      <td>${row.risk_level}</td>
      <td>${row.category}</td>
    </tr>
  `);

  renderTable('walk-forward-table', ['Symbol', 'Cutoff', 'Status', 'Decision', 'Train Bars', 'Test Bars', 'Test Return', 'Reason'], wf, (row) => `
    <tr>
      <td>${nameOf(row.symbol)}</td>
      <td>${row.cutoff || '-'}</td>
      <td>${row.status}</td>
      <td>${row.decision || '-'}</td>
      <td>${row.train_bars ?? '-'}</td>
      <td>${row.test_bars ?? '-'}</td>
      <td>${pct(row.out_of_sample_test?.total_return_pct)}</td>
      <td>${(row.reasons || []).join('<br>') || '-'}</td>
    </tr>
  `);

  renderTable('backtest-runs-table', ['Time', 'Symbol', 'Strategy', 'Return', 'B&H', 'MDD', 'Trades', 'PF'], runs.items || [], (row) => `
    <tr>
      <td>${new Date(row.run_at).toLocaleString()}</td>
      <td>${nameOf(row.symbol)}</td>
      <td>${row.strategy}</td>
      <td>${pct(row.total_return_pct)}</td>
      <td>${pct(row.buy_hold_return_pct)}</td>
      <td>${pct(row.max_drawdown_pct)}</td>
      <td>${row.trade_count}</td>
      <td>${fmt(row.profit_factor)}</td>
    </tr>
  `);
  window.paperTraderMonitorLoaded = true;
}

setupMonitorTabs();
setupDetailTabs();
setupSymbolSplitResize();
document.addEventListener('click', (event) => {
  if (event.target.closest('[data-close-symbol-review]')) {
    const drawer = document.getElementById('symbol-review-drawer');
    if (drawer) drawer.classList.remove('is-open');
    return;
  }
  if (event.target.closest('[data-close-inline-review]')) {
    const symbol = selectedUniverseSymbol;
    expandedSymbolReviewId = null;
    if (symbol) renderSymbolOverview(symbol).catch(console.error);
    return;
  }
  const drillLink = event.target.closest('.symbol-drill-link');
  if (drillLink?.dataset.symbol) {
    switchMonitorTab('symbols');
    renderSymbolOverview(drillLink.dataset.symbol).catch((err) => {
      setHtml('symbol-review-summary', `<b>요약 로딩 실패</b>: ${err.message}`);
    });
    return;
  }
  const universeCard = event.target.closest('.symbol-universe-card');
  if (universeCard?.dataset.symbol) {
    switchMonitorTab('symbols');
    renderSymbolOverview(universeCard.dataset.symbol).catch((err) => {
      setHtml('symbol-review-summary', `<b>요약 로딩 실패</b>: ${err.message}`);
    });
    return;
  }
  const historyRow = event.target.closest('.symbol-review-history-row');
  if (historyRow?.dataset.reviewId) {
    const id = String(historyRow.dataset.reviewId);
    expandedSymbolReviewId = String(expandedSymbolReviewId || '') === id ? null : id;
    switchMonitorTab('symbols');
    if (historyRow.dataset.symbol) renderSymbolOverview(historyRow.dataset.symbol).catch((err) => setHtml('symbol-review-summary', `<b>이력 로딩 실패</b>: ${err.message}`));
    return;
  }
  const card = event.target.closest('.history-symbol-card');
  if (!card?.dataset.symbol) return;
  const input = document.getElementById('symbol-review-input');
  if (input) input.value = card.dataset.symbol;
  switchMonitorTab('symbols');
  const payload = card.dataset.reviewId ? symbolReviewPayloadById.get(String(card.dataset.reviewId)) : null;
  if (payload) renderSymbolReviewDetail(payload, '검토이력');
  else runSymbolReview();
});
document.getElementById('symbol-review-btn')?.addEventListener('click', () => runSymbolReview());
document.addEventListener('click', async (event) => {
  const btn = event.target.closest('[data-add-reviewed-symbol]');
  if (!btn) return;
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = '편입 중...';
  try {
    await addReviewedSymbolToUniverse(btn.dataset.addReviewedSymbol, 'watch');
    btn.textContent = '편입됨';
  } catch (err) {
    btn.disabled = false;
    btn.textContent = original || 'Watch 편입';
    alert('Universe 편입 실패: ' + err.message);
  }
});
document.getElementById('symbol-review-input')?.addEventListener('keydown', (event) => { if (event.key === 'Enter') runSymbolReview(); });
document.getElementById('market-filter')?.addEventListener('change', (event) => {
  selectedRecommendationMarket = event.target.value || 'KR';
  storeRecommendationMarket(selectedRecommendationMarket);
  load().catch(alert);
});
document.getElementById('strategy-status-filter')?.addEventListener('change', (event) => {
  strategyFilterStatus = event.target.value || 'all';
  load().catch(alert);
});
document.getElementById('strategy-sort')?.addEventListener('change', (event) => {
  strategySortKey = event.target.value || 'status';
  load().catch(alert);
});
document.getElementById('strategy-search')?.addEventListener('input', (event) => {
  strategySearchText = event.target.value || '';
  load().catch(console.error);
});
document.getElementById('universe-market-filter')?.addEventListener('change', (event) => {
  universeMarketFilter = event.target.value || 'KR';
  storeUniverseMarket(universeMarketFilter);
  load().catch(alert);
});
document.getElementById('universe-status-filter')?.addEventListener('change', (event) => {
  universeStatusFilter = event.target.value || 'all';
  load().catch(alert);
});
document.getElementById('universe-sort')?.addEventListener('change', (event) => {
  universeSortKey = event.target.value || 'score';
  load().catch(alert);
});
document.getElementById('universe-search')?.addEventListener('input', (event) => {
  universeSearchText = event.target.value || '';
  load().catch(console.error);
});
document.getElementById('audit-result-filter')?.addEventListener('change', (event) => {
  auditResultFilter = event.target.value || 'all';
  load().catch(alert);
});
document.getElementById('audit-symbol-filter')?.addEventListener('input', (event) => {
  auditSymbolFilter = event.target.value || '';
  load().catch(console.error);
});
load().catch((err) => { console.error(err); setHtml('audit-summary-text', `<b>화면 로딩 오류</b>: ${err.message}`); });
setInterval(() => load().catch(console.error), 120_000);

function setupFundTradeEvents(){
  document.addEventListener('click',(event)=>{const btn=event.target.closest('.fund-trades-btn'); if(btn) openFundTrades(btn.dataset.fundId||'');});
  document.addEventListener('input',(event)=>{if(event.target && event.target.id==='fund-trade-filter') renderFundTradeHistory(event.target.value||'');});
}
setupFundTradeEvents();

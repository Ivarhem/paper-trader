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
  const lowWin = Number(vb.avg_excess_win_rate_pct || 0) > 0 && Number(vb.avg_excess_win_rate_pct || 0) < 50;
  if (risk.decision === 'pass') return {label:'승인 통과', kind:'good', reason:'paper-buy 승인 조건을 통과했습니다.'};
  if (hard) return {label:'승인 차단', kind:'bad', reason: critic.summary || 'critic high / hard risk가 있어 paper-buy 승인을 차단합니다.'};
  if (hasTail) return {label:'승인 보류', kind:'warn', reason:'tail-risk / negative EV / 평균 초과수익 플래그가 남아 있어 paper-buy 승인은 보류합니다.'};
  if (under || noEdge) return {label:'검증대기', kind:'warn', reason:'종목별 샘플 또는 positive edge가 부족해 추가 검증이 필요합니다.'};
  if (lowWin) return {label:'승인 보류', kind:'warn', reason:'조건 라벨이 아직 paper-buy 승인에는 보수적으로 해석됩니다.'};
  if (risk.decision === 'blocked') return {label:'승인 보류', kind:'warn', reason:'RiskGate가 paper-buy 승인에는 아직 충분하지 않다고 판단했습니다.'};
  if (risk.decision === 'needs_more_validation') return {label:'검증대기', kind:'warn', reason:'추가 검증 후 재평가가 필요한 후보입니다.'};
  return {label:risk.decision || '검토 전', kind:'neutral', reason:'RiskGate 검토 전입니다.'};
}

function auditFlagLabel(flag, includeRaw = false) {
  const raw = String(flag || '-');
  const label = {
    left_tail_excess_risk: '좌측꼬리 위험',
    negative_expected_excess_value: '음수 기대값',
    no_positive_average_excess: '평균 초과수익 없음',
    period_instability: '기간 안정성 부족',
    unfavorable_payoff_asymmetry: '손익비 불리',
    no_candidate_buy_signals: '매수 신호 부족',
    weak_success_confidence_interval: '성공률 신뢰구간 약함',
    recent_decay: '최근 성과 둔화',
  }[flag] || raw.replaceAll('_', ' ');
  return includeRaw && raw && label !== raw ? `${label} (${raw})` : label;
}

function auditFlagDescription(flag) {
  return {
    left_tail_excess_risk: '실패 구간의 하방 손실이 커서 승인 게이트가 보수적으로 봅니다.',
    negative_expected_excess_value: '벤치마크 대비 기대 초과수익이 아직 음수입니다.',
    no_positive_average_excess: '검증 평균이 기준지수 대비 우위를 충분히 보이지 못했습니다.',
    period_instability: '일부 기간에서만 작동하거나 성과 일관성이 약합니다.',
    unfavorable_payoff_asymmetry: '목표수익 대비 손실/변동성 부담이 큽니다.',
    no_candidate_buy_signals: '현재 조건에서 매수 후보 신호가 충분히 잡히지 않았습니다.',
    weak_success_confidence_interval: '성공률 통계 신뢰도가 낮아 추가 샘플이 필요합니다.',
    recent_decay: '최근 구간 성과가 이전보다 둔화됐습니다.',
  }[flag] || '추가 검증에서 추적할 audit 품질 플래그입니다.';
}

function auditReliabilityProfile(row = {}, vb = {}) {
  const contract = row.audit_reliability_contract || vb.audit_reliability_contract || {};
  const explicitTags = row.audit_reliability_tags || vb.audit_reliability_tags || contract.labels || [];
  if (explicitTags.length) return {score: contract.trust_axes?.regime_fit ?? vb.audit_quality_min_score ?? row.quality_score ?? 0, samples: vb.symbol_validation_sample_count ?? row.samples ?? 0, tags: explicitTags};
  const audit = row.presentation?.audit_summary || {};
  const flags = audit.flags || vb.audit_quality_flags || row.quality_flags || [];
  const score = Number(audit.min_quality_score ?? vb.audit_quality_min_score ?? row.quality_score ?? 0);
  const samples = Number(vb.symbol_validation_sample_count ?? row.samples ?? 0);
  const excess = Number(vb.avg_excess_win_rate_pct ?? row.excess_win_rate_pct ?? row.evaluation_success_rate_pct ?? 0);
  const tags = [];
  if (score >= 70 && samples >= 30 && excess >= 50) tags.push({label:'신뢰 가능 구간', kind:'good', desc:'현재 표본에서는 추천 보조 근거로 쓸 수 있습니다.'});
  if (flags.includes('left_tail_excess_risk')) tags.push({label:'하락장/급락 취약', kind:'warn', desc:'손실 꼬리가 커서 방어형 fund에는 약하게 반영합니다.'});
  if (flags.includes('negative_expected_excess_value') || flags.includes('no_positive_average_excess')) tags.push({label:'초과수익 신뢰 낮음', kind:'bad', desc:'벤치마크 대비 기대값이 약해 승인보다 연구/관찰에 가깝습니다.'});
  if (flags.includes('period_instability') || flags.includes('recent_decay')) tags.push({label:'국면 의존', kind:'warn', desc:'특정 기간/최근 구간에서 성과가 흔들려 regime 라벨과 함께 봐야 합니다.'});
  if (flags.includes('weak_success_confidence_interval') || samples < 30) tags.push({label:'표본 부족', kind:'warn', desc:'성공률보다 신뢰구간/샘플 축적이 우선입니다.'});
  if (!tags.length) tags.push({label:'검증 특이점 낮음', kind:'neutral', desc:'치명적 플래그는 제한적이나 regime별 성과는 계속 분리 관찰합니다.'});
  return {score, samples, tags};
}

function auditEvidenceBlock(row, vb) {
  const audit = row.presentation?.audit_summary || {};
  const flags = audit.flags || vb.audit_quality_flags || [];
  const reliability = auditReliabilityProfile(row, vb);
  const rows = flags.slice(0, 6).map((flag) => ({item:auditFlagLabel(flag, true), state:'주의', kind:'warn', desc:auditFlagDescription(flag)}));
  const leadingTags = (reliability.tags || []).slice(0, 3).map((r) => ({label:'전략 라벨', value:r.label, kind:r.kind || 'neutral'}));
  return `<div class="audit-evidence-block">
    ${evidencePills(leadingTags.length ? leadingTags : [{label:'전략 라벨', value:'검증 특이점 낮음', kind:'neutral'}])}
    <ul class="audit-quality-list committee-action-list">
      <li class="audit-quality-header"><b>신뢰/국면 라벨</b><span>상태</span><em>설명</em></li>
      ${reliability.tags.map((r) => `<li class="${r.kind || 'neutral'}"><b>${r.label}</b><span class="${r.kind || 'neutral'}">${r.kind === 'good' ? '반영' : r.kind === 'bad' ? '차단' : '주의'}</span><em>${r.description || r.desc || ''}</em></li>`).join('')}
      ${rows.length ? `<li class="audit-quality-header"><b>주의 근거</b><span>상태</span><em>설명</em></li>${rows.map((r) => `<li class="${r.kind || 'neutral'}"><b>${r.item}</b><span class="${r.kind || 'neutral'}">${r.state}</span><em>${readableEvidenceText(r.desc)}</em></li>`).join('')}` : ''}
    </ul>
  </div>`;
}

function committeeEvidenceBlock(row, fallbackText='') {
  const committee = row.investment_committee || {};
  const syn = committee.synthesis || {};
  const opinions = committee.opinions || [];
  const parts = committeeSplit(row, fallbackText);
  const decision = syn.decision || row.committee_decision || '';
  const decisionLabel = committeeDecisionLabel(decision);
  const research = syn.research_committee || {};
  const risk = syn.risk_gate || {};
  const rows = [];
  const riskView = riskGateDisplay(row, risk);
  if (research.decision || research.score !== undefined) rows.push({label:'연구판단', value:`${researchDecisionLabel(research.decision)} · ${fmt(research.score)}`, kind:String(research.decision || '').includes('support') ? 'good' : (research.decision === 'ignore' ? 'neutral' : 'warn')});
  if (risk.decision || risk.score !== undefined) rows.push({label:'승인게이트', value:`${riskView.label} · ${fmt(risk.score)}`, kind:riskView.kind});
  if (syn.market_context?.market_stress_reasons?.length) rows.push({label:'보류맥락', value:syn.market_context.market_stress_reasons.slice(0, 2).join(' · '), kind:riskView.kind === 'good' ? 'neutral' : 'warn'});
  if (!rows.length && decision) rows.push({label:'위원회', value:decisionLabel, kind:decision === 'committee_support' ? 'good' : (decision === 'reject' ? 'warn' : 'neutral')});
  const actionLabel = (o) => ({support:'지지', oppose:'반대', watch:'관찰'}[o.opinion] || o.opinion || '-');
  const reasonText = (o) => (o.concerns || o.supports || []).filter(Boolean).slice(0, 2).join(' · ') || o.reason || o.rationale || '';
  const opinionRows = opinions
    .filter((o) => ['support','oppose','watch'].includes(o.opinion))
    .sort((a,b) => ({support:0, oppose:1, watch:2}[a.opinion] ?? 3) - ({support:0, oppose:1, watch:2}[b.opinion] ?? 3))
    .map((o) => `<li class="${o.opinion || ''}"><b>${o.label || o.agent || 'agent'}</b><span class="${o.opinion || ''}">${actionLabel(o)}</span><em>${reasonText(o)}</em></li>`).join('');
  const grouped = ['support','oppose'].map((op) => {
    const names = opinions.filter((o) => o.opinion === op).map((o) => o.label || o.agent).filter(Boolean);
    if (!names.length) return '';
    return `<p class="${op}"><b>${op === 'support' ? '지지' : '반대'}</b><span>${names.join(' · ')}</span></p>`;
  }).join('');
  const split = grouped ? `<div class="committee-readable-split committee-members-only">${grouped}</div>` : '';
  const summary = syn.readable_summary || syn.summary || fallbackText || row.regime_gate?.reason || '위원회/게이트 추가 검토 전';
  const approvalNote = riskView ? `<p class="risk-gate-readable ${riskView.kind}"><b>${riskView.label}</b><span>${riskView.reason}</span></p>` : '';
  const aggressiveNote = syn.market_context?.aggressive_note ? `<p class="risk-gate-readable neutral"><b>공격형</b><span>${syn.market_context.aggressive_note}</span></p>` : '';
  return `<div class="committee-evidence-block ${decision || 'pending'} ${riskView?.kind || ''}">
    <div class="committee-evidence-head"><span>위원회 판단</span><b>${decisionLabel}</b></div>
    ${rows.length ? evidencePills(rows) : ''}
    ${approvalNote}
    ${aggressiveNote}
    ${split || `<p class="evidence-readable-line">${summary}</p>`}
    ${opinionRows ? `<ul class="committee-opinion-list committee-action-list"><li class="committee-action-header"><b>위원회</b><span>제안액션</span><em>근거</em></li>${opinionRows}</ul>` : ''}
  </div>`;
}




function goMonitorTab(tab, selector='') {
  switchMonitorTab(tab);
  if (selector) {
    setTimeout(() => document.querySelector(selector)?.scrollIntoView({behavior:'smooth', block:'start'}), 80);
  }
}


function evidenceCodeLabel(code) {
  const raw = String(code || '').trim();
  if (!raw) return '-';
  const label = {
    obv_cmf_mfi_volume_confirmation_proxy_slightly_upweighted_validation_gated: 'OBV/CMF/MFI/거래량 확인 proxy 약한 가중치',
    naver_foreign_institution_seed_slight_monitoring_boost_validation_gated: '네이버 외국인·기관 seed 약한 모니터링 가중치',
    supply_close_or_technical_proxy_plus_investor_flow_seed_validation_gated: '수급/종가강도 proxy + 거래주체 seed 검증제한 반영',
    volume_confirmation: '거래량 확인',
    obv_rising: 'OBV 상승',
    positive_cmf: 'CMF 양수',
    technical_supply_proxy: '기술적 수급 proxy',
    investor_flow_seed_proxy: '거래주체 seed proxy',
    paper_monitoring_seed_only: 'paper 모니터링 seed 전용',
    not_available_in_local_db: '로컬 DB 미연동',
    not_in_investor_flow_seed: '거래주체 seed에 없음',
    investor_flow_data_not_ingested: '거래주체 데이터 미수집',
    trend: '추세형',
    foreign: '외국인',
    institution: '기관',
    individual: '개인',
  }[raw] || raw.replaceAll('_', ' ');
  return label === raw ? label : `${label} (${raw})`;
}

function evidenceCodeListText(value) {
  if (Array.isArray(value)) return value.map(evidenceCodeLabel).join(' · ');
  const raw = String(value || '');
  if (!raw) return '-';
  return raw.split(/\s*·\s*|\s*,\s*/).filter(Boolean).map(evidenceCodeLabel).join(' · ');
}

function fundEvidenceBlock(row, vb) {
  const fund = vb.fund_consensus || {};
  const style = vb.fund_style_consensus || {};
  const funds = fund.funds || [];
  const has = fund && Object.keys(fund).length;
  const styleText = Object.entries(style).map(([k,v]) => `${evidenceCodeLabel(k)} ${v}`).join(' · ') || '스타일 합의 없음';
  if (!has) return `<div class="fund-evidence-block"><p class="evidence-readable-line">이 종목을 지지한 Fund consensus가 아직 없습니다.</p><small>Fund league/price replay는 별도로 동작 중이며, 현재 카드는 전략/Risk/시장 evidence를 우선 표시합니다.</small></div>`;
  const rows = [
    {item:'Fund 지지', state:`${fund.votes ?? fund.vote_count ?? 0}표`, kind:Number(fund.votes ?? fund.vote_count ?? 0) >= 10 ? 'good' : 'warn', desc:`관찰 fund 중 이 종목을 보유/선호한 합의 표입니다. 가중점수 ${fmt(fund.weighted_score)}.`},
    {item:'점수 보정', state:`+${fmt(vb.fund_consensus_score_boost || 0)}`, kind:Number(vb.fund_consensus_score_boost || 0) > 0 ? 'good' : 'neutral', desc:'추천 점수에 더해지는 fund consensus 보조 boost입니다.'},
    {item:'스타일 합의', state:`+${fmt(vb.fund_style_consensus_boost_total || 0)}`, kind:Number(vb.fund_style_consensus_boost_total || 0) > 0 ? 'good' : 'neutral', desc:styleText},
    {item:'참여 Fund', state:`${funds.length}개`, kind:funds.length ? 'neutral' : 'warn', desc:funds.slice(0, 6).map((id) => `가격 replay Fund (${id})`).join(' · ') || '표시 가능한 fund id 없음'},
  ];
  return `<div class="fund-evidence-block">
    ${evidencePills([
      {label:'Votes', value:`${fund.votes ?? fund.vote_count ?? 0}`, kind:Number(fund.votes ?? fund.vote_count ?? 0) >= 10 ? 'good' : 'warn'},
      {label:'가중점수', value:fmt(fund.weighted_score), kind:Number(fund.weighted_score || 0) >= 30 ? 'good' : 'neutral'},
      {label:'점수보정', value:`+${fmt(vb.fund_consensus_score_boost || 0)}`, kind:Number(vb.fund_consensus_score_boost || 0) > 0 ? 'good' : 'neutral'},
      {label:'스타일', value:Object.keys(style)[0] || '-', kind:Object.keys(style).length ? 'good' : 'neutral'}
    ])}
    <ul class="fund-evidence-list audit-quality-list">
      <li class="fund-evidence-header"><b>항목</b><span>상태</span><em>설명</em></li>
      ${rows.map((r) => `<li class="${r.kind || 'neutral'}"><b>${r.item}</b><span class="${r.kind || 'neutral'}">${r.state}</span><em>${r.desc}</em></li>`).join('')}
    </ul>
    <div class="evidence-link-row"><button type="button" onclick="goMonitorTab('funds','[data-tab-panel=\'funds\']')">Fund 엔진에서 보기</button><small>Fund consensus와 가격 replay가 현재 추천 후보 선정의 1차 근거입니다.</small></div>
  </div>`;
}

function compactEvidenceStatus(row, vb = {}, blockers = [], human = {}) {
  const syn = row.investment_committee?.synthesis || {};
  const risk = syn.risk_gate || {};
  const research = syn.research_committee || {};
  const riskView = riskGateDisplay(row, risk);
  const fund = vb.fund_consensus || syn.fund_consensus || {};
  const fundVotes = Number(fund.votes ?? fund.vote_count ?? 0);
  const styleCount = Object.keys(vb.fund_style_consensus || {}).length;
  const auditScore = row.presentation?.audit_summary?.min_quality_score ?? vb.audit_quality_min_score;
  const auditProfile = auditReliabilityProfile(row, vb);
  const auditLabel = (auditProfile.tags || [])[0]?.label || '조건 라벨 확인';
  const auditHint = (auditProfile.tags || []).slice(1, 3).map((x) => x.label).join(' · ') || '상세에서 국면/주의 라벨 확인';
  const committeeLabel = committeeDecisionLabel(syn.decision || row.committee_decision);
  const committeeKind = syn.decision === 'committee_support' ? 'good' : (syn.decision === 'reject' ? 'warn' : 'neutral');
  const fundKind = fundVotes > 0 ? 'good' : (styleCount ? 'warn' : 'neutral');
  const fundLabel = fundVotes > 0 ? `${fundVotes}표 · score ${fmt(fund.weighted_score)}` : (styleCount ? '스타일 proxy만 있음' : '종목 합의 없음');
  const auditKind = Number(auditScore || 0) >= 70 ? 'good' : (Number(auditScore || 0) >= 50 ? 'warn' : 'bad');
  const riskText = readableRiskSummary(blockers.length ? blockers.slice(0, 1).join('') : (human.mainRisk || '추가 주의사항 낮음'));
  return `<div class="rec-evidence-strip" aria-label="추천 판단 근거 요약">
    <span class="warn"><b>핵심 주의</b><em>${riskText}</em><small>가격 계획과 분리된 리스크 요약</small></span>
    <span class="${committeeKind}"><b>위원회</b><em>${committeeLabel}</em><small>${research.decision ? `Research ${researchDecisionLabel(research.decision)} ${fmt(research.score)}` : '검토 전'}</small></span>
    <span class="${riskView.kind}"><b>Risk Gate</b><em>${riskView.label}</em><small>${risk.score !== undefined ? `score ${fmt(risk.score)}` : riskView.reason}</small></span>
    <span class="${fundKind}"><b>Fund consensus</b><em>${fundLabel}</em><small>선정 근거 · 가격 합의 확인</small></span>
    <span class="${auditKind}"><b>조건 라벨</b><em>${auditLabel}</em><small>${auditHint}</small></span>
  </div>`;
}

function entryPlanBlock(row = {}) {
  const plan = row.entry_plan || row.validation_basis?.entry_plan || {};
  if (!plan || !Object.keys(plan).length) return '';
  const rr = plan.reward_risk_from_target_buy;
  const mode = plan.label || '분할 진입 기준';
  const posture = pricePostureBadge(row);
  return `<section class="price-plan-section entry" aria-label="진입 계획">
    <div class="price-plan-section-head"><b>진입 계획</b><span>${mode}</span>${posture}</div>
    <div class="price-plan-cells">
      <div class="${pricePlanCellClass(row, 'entry_lower', ['entry_band'])}"><span>목표 매입가</span><b>${fmt(plan.target_buy_price)}</b><em>${pct(plan.pullback_from_analysis_price_pct)} 하단 대기</em></div>
      <div class="${pricePlanCellClass(row, 'entry_upper', ['entry_band'])}"><span>진입 상단</span><b>${fmt(plan.acceptable_entry_upper)}</b><em>${pct(plan.acceptable_entry_pullback_pct)} 이상은 관망</em></div>
      <div class="${pricePlanCellClass(row, 'chase_above', [], 'risk')}"><span>추격 금지선</span><b>${fmt(plan.chase_above_price)}</b><em>이상 추격 금지</em></div>
      <div><span>보상/위험</span><b>${fmt(rr)}</b><em>진입 구간 기준</em></div>
    </div>
  </section>`;
}

function priceHighlightTargets(row = {}) {
  const syn = row.investment_committee?.synthesis || {};
  const raw = row.highlight_targets || syn.highlight_targets || row.validation_basis?.highlight_targets || [];
  return new Set(Array.isArray(raw) ? raw : []);
}

function hasPriceHighlight(row, key, aliases = []) {
  const targets = priceHighlightTargets(row);
  return targets.has(key) || aliases.some((alias) => targets.has(alias));
}

function pricePlanCellClass(row, key, aliases = [], base = '') {
  return [base, hasPriceHighlight(row, key, aliases) ? 'decision' : ''].filter(Boolean).join(' ');
}

function postureLabel(posture) {
  return {
    defensive: '보수 강조',
    balanced: '균형 강조',
    aggressive: '공격 강조',
    avoid: '리스크 강조',
    take_profit: '실현 강조',
  }[posture] || '';
}

function pricePostureBadge(row = {}) {
  const syn = row.investment_committee?.synthesis || {};
  const posture = row.committee_posture || syn.committee_posture;
  const label = postureLabel(posture);
  const reason = row.posture_reason || syn.posture_reason || '';
  if (!label && !reason) return '';
  return `<em class="price-posture ${posture || 'neutral'}" title="${reason || label}">${label || '강조 기준'}</em>`;
}

function pricePlanBlock(row = {}) {
  return `<div class="rec-product-price-plan" aria-label="추천 가격 계획">
    <section class="price-plan-section context" aria-label="현재 가격 기준">
      <div class="price-plan-section-head"><b>현재가</b><span>${priceDateLabel(row)}</span></div>
      <div class="price-plan-cells one">
        <div class="${pricePlanCellClass(row, 'current_price', [], 'reference')}"><span>분석 기준가</span><b>${fmt(row.last_price)}</b><em>현재 가격 기준</em></div>
      </div>
    </section>
    ${entryPlanBlock(row)}
    <section class="price-plan-section exit" aria-label="이탈 계획">
      <div class="price-plan-section-head"><b>가격 계획</b><span>${priceDateLabel(row)}</span></div>
      <div class="price-plan-cells">
        <div class="${pricePlanCellClass(row, 'target_1')}"><span>1차 실현 기준</span><b>${fmt(row.target_1 || row.conservative_target_price || row.target_price)}</b><em>${pct(row.upside_1_pct || row.upside_target_1_pct || row.upside_pct)}</em></div>
        <div class="${pricePlanCellClass(row, 'target_2')}"><span>중심 목표</span><b>${fmt(row.target_2 || row.adjusted_target_price || row.target_price)}</b><em>${pct(row.upside_2_pct || row.upside_target_2_pct || row.upside_pct)}</em></div>
        <div class="${pricePlanCellClass(row, 'target_3')}"><span>확장 목표</span><b>${fmt(row.target_3 || row.optimistic_target_price || row.target_price)}</b><em>${pct(row.upside_3_pct || row.upside_target_3_pct || row.upside_pct)}</em></div>
        <div class="${pricePlanCellClass(row, 'stop_reference', [], 'risk wide')}"><span>무효화·손절 기준</span><b>${fmt(row.stop_reference)}</b><em>${pct(row.downside_stop_pct)} 이탈 시 계획 무효</em></div>
      </div>
    </section>
  </div>`;
}

function supplyEvidenceBlock(row, vb) {
  const adj = vb.supply_close_score_adjustment || {};
  const flow = vb.investor_flow_seed_context || {};
  const flowAdj = adj.investor_flow_adjustment || {};
  const flags = adj.flags || [];
  const investors = flow.investors || flowAdj.investors || [];
  const sources = flow.sources || [];
  const status = vb.investor_flow_status || (flow.symbol ? 'seed_proxy' : 'not_available');
  const isRealDb = status && !String(status).includes('not_available');
  const rows = [
    {item:'데이터 상태', state:isRealDb ? '잠정 seed' : '미연동', kind:isRealDb ? 'warn' : 'neutral', desc:isRealDb ? '거래주체 seed/proxy 신호만 보조 참고로 사용합니다.' : '로컬 DB의 실제 외국인/기관/개인 순매수 데이터는 아직 추천 근거로 미사용입니다.'},
    {item:'수급 보정', state:`+${fmt(vb.supply_close_score_adjustment_pct ?? adj.adjustment ?? 0)}%`, kind:Number(vb.supply_close_score_adjustment_pct ?? adj.adjustment ?? 0) > 0 ? 'good' : 'neutral', desc:adj.combined_reason ? evidenceCodeLabel(adj.combined_reason) : (adj.reason ? evidenceCodeLabel(adj.reason) : (vb.supply_close_explanation || '가격·거래량 기반 proxy 보정입니다.'))},
    {item:'기술 proxy', state:`+${fmt(vb.supply_close_base_adjustment_pct ?? adj.base_adjustment ?? 0)}%`, kind:Number(vb.supply_close_base_adjustment_pct ?? adj.base_adjustment ?? 0) > 0 ? 'good' : 'neutral', desc:flags.length ? evidenceCodeListText(flags) : 'OBV/CMF/MFI/거래량 확인 신호가 제한적입니다.'},
    {item:'거래주체 seed', state:`+${fmt(vb.investor_flow_seed_adjustment_pct ?? flowAdj.adjustment ?? 0)}%`, kind:Number(vb.investor_flow_seed_adjustment_pct ?? flowAdj.adjustment ?? 0) > 0 ? 'warn' : 'neutral', desc:investors.length ? `${investors.map(evidenceCodeLabel).join(' · ')} 순위/관심 seed 감지 · 실거래 판단 아님${flowAdj.reason ? ` · ${evidenceCodeLabel(flowAdj.reason)}` : ''}` : '거래주체 seed 신호 없음'},
  ];
  sources.slice(0, 3).forEach((src) => rows.push({item:evidenceCodeLabel(src.investor || 'source'), state:src.rank ? `잠정 ${src.rank}위` : '-', kind:'neutral', desc:src.raw_text || 'raw source text 없음'}));
  return `<div class="supply-evidence-block">
    ${evidencePills([
      {label:'상태', value:isRealDb ? 'seed/proxy' : '미연동', kind:isRealDb ? 'warn' : 'neutral'},
      {label:'수급보정', value:`+${fmt(vb.supply_close_score_adjustment_pct ?? adj.adjustment ?? 0)}%`, kind:Number(vb.supply_close_score_adjustment_pct ?? adj.adjustment ?? 0) > 0 ? 'good' : 'neutral'},
      {label:'거래주체', value:investors.length ? investors.map(evidenceCodeLabel).join('/') : '-', kind:investors.length ? 'warn' : 'neutral'},
      {label:'권한', value:flow.authority ? evidenceCodeLabel(flow.authority) : '보조근거만', kind:'neutral'}
    ])}
    <ul class="supply-evidence-list audit-quality-list">
      <li class="supply-evidence-header"><b>항목</b><span>상태</span><em>설명</em></li>
      ${rows.map((r) => `<li class="${r.kind || 'neutral'}"><b>${r.item}</b><span class="${r.kind || 'neutral'}">${r.state}</span><em>${readableEvidenceText(r.desc)}</em></li>`).join('')}
    </ul>
    <div class="evidence-link-row"><button type="button" onclick="goMonitorTab('org','.supply-weight-summary')">조직/가중치 검증에서 보기</button><small>${vb.supply_close_explanation || '수급/거래주체 정보는 추천을 대체하지 않고 검증 보조 evidence로만 사용합니다.'}</small></div>
  </div>`;
}


function marketRiskLabel(risk = '') {
  const v = String(risk || 'normal');
  if (v.includes('high_chase')) return '추격매수 위험 높음';
  if (v.includes('moderate_chase')) return '추격매수 주의';
  if (v.includes('news_only')) return '뉴스 단독 관찰';
  if (v.includes('blocked')) return '위험 게이트 차단';
  if (v === 'normal' || !v) return '일반';
  return evidenceCodeLabel(v);
}

function marketPolicyLabel(ctx = {}) {
  const p = String(ctx.recommendation_policy || ctx.recency_policy || '');
  if (p.includes('short_term_boost')) return '단기 이슈';
  if (p.includes('long_term')) return '장기 참고';
  if (p.includes('watch_only')) return '관찰 전용';
  if (p.includes('context_boost')) return '보조 반영';
  return '';
}

function marketScopeLabel(item = {}, row = {}) {
  const symbol = String(row.symbol || '');
  const name = String(row.name || symbolNames[symbol] || '').replace(/\s*\([^)]*\)\s*$/, '');
  const titleBlob = decodeMarketText([item.label,item.theme,item.title,item.headline,item.narrative,(item.sources||[]).map(s=>s.title).join(' ')].filter(Boolean).join(' '));
  if ((name && titleBlob.includes(name)) || (symbol && titleBlob.includes(symbol.replace(/\.(KS|KQ)$/,'')))) return '직접 이슈';
  return '섹터/시장 이슈';
}

function marketSourceLine(src, row = {}) {
  const title = decodeMarketText(src.title || src.headline || src.label || src);
  const meta = [];
  const domain = src.domain || (src.url ? (() => { try { return new URL(src.url).hostname.replace(/^www\./,''); } catch (_) { return ''; } })() : '');
  if (domain) meta.push(domain);
  if (src.published_at) meta.push(src.published_at);
  return `[${title}${meta.length ? ` · ${meta.join(' · ')}` : ''}]`;
}

function marketMetricLine(ctx = {}) {
  const parts = [];
  const oneDay = ctx.us_avg_1d_pct ?? ctx.avg_1d_pct ?? ctx.return_1d_pct;
  const fiveDay = ctx.us_avg_5d_pct ?? ctx.avg_5d_pct ?? ctx.return_5d_pct;
  const volume = ctx.volume_ratio_20d ?? ctx.avg_volume_ratio ?? ctx.volume_ratio;
  const breadth = ctx.us_breadth_positive_pct ?? ctx.breadth_positive_pct;
  const impact = ctx.impact_score ?? ctx.score;
  if (oneDay !== undefined && oneDay !== null && oneDay !== '') parts.push(`1D ${fmt(oneDay)}%`);
  if (fiveDay !== undefined && fiveDay !== null && fiveDay !== '') parts.push(`5D ${fmt(fiveDay)}%`);
  if (volume !== undefined && volume !== null && volume !== '') parts.push(`거래량 ${fmt(volume)}배`);
  if (breadth !== undefined && breadth !== null && breadth !== '') parts.push(`상승비율 ${fmt(breadth)}%`);
  if (impact !== undefined && impact !== null && impact !== '') parts.push(`이슈 영향도 ${fmt(impact)}`);
  if (parts.length) return `관련 종목군 평균 ${parts.join(', ')}.`;
  const summary = String(ctx.summary || '');
  const metricBits = summary.match(/(?:1D|5D|breadth|impact)[^,·]*/gi) || [];
  return metricBits.length ? `관련 종목군 평균 ${metricBits.join(', ')}.` : '';
}

function marketNarrativeText(ctx = {}, fallback = '') {
  const raw = ctx.narrative || ctx.risk_note || fallback || ctx.summary || '추가 시장 설명 없음';
  const summary = String(ctx.summary || '');
  if (raw === summary) return summary.replace(/:\s*1D.*$/i, '').trim() || raw;
  return raw;
}


function decodeMarketText(v = '') {
  return String(v || '').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
}


function marketSentimentView(item = {}) {
  const risk = String(item.risk || item.gap_chase_risk || '').toLowerCase();
  const policy = String(item.recommendation_policy || item.recency_policy || '').toLowerCase();
  const score = Number(item.impact_score ?? item.score ?? 0);
  if (risk.includes('high') || risk.includes('blocked') || policy.includes('long_term') || policy.includes('watch_only')) return {label:'주의', kind:'warn'};
  if (risk.includes('moderate') || risk.includes('chase') || score >= 80) return {label:'중립/주의', kind:'warn'};
  if (score > 0 || String(item.expected_impact || '').toLowerCase().includes('positive')) return {label:'긍정', kind:'good'};
  return {label:'중립', kind:'neutral'};
}

function cleanMarketNarrative(ctx = {}, title = '', fallback = '') {
  let text = decodeMarketText(ctx.narrative || ctx.risk_note || ctx.summary || fallback || '추가 설명 없음').trim();
  if (title) {
    const esc = String(title).replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s*');
    text = text.replace(new RegExp(`^${esc}\\s*[:：-]\\s*`, 'i'), '');
  }
  text = text.replace(/\s*평균\s+1D\s+[-+\d.]+%\s*,\s*5D\s+[-+\d.]+%\s*,\s*거래량\s+[-+\d.]+배\s*\.?\s*$/i, '').trim();
  text = text.replace(/\s*1D\s+[-+\d.]+%\s*,\s*5D\s+[-+\d.]+%\s*,\s*(?:breadth\s+[-+\d.]+%\s*,\s*)?impact\s+[-+\d.]+\s*\.?\s*$/i, '').trim();
  if (text.includes(' / ') && !/[.!?。]\s*$/.test(text)) return '뉴스 기사 기반 시장 이슈입니다.';
  return text || '추가 설명 없음';
}

function marketMetricFromNarrative(ctx = {}) {
  const text = String(ctx.narrative || ctx.summary || '');
  const ko = text.match(/평균\s+1D\s+[-+\d.]+%\s*,\s*5D\s+[-+\d.]+%\s*,\s*거래량\s+[-+\d.]+배\s*\.?/i);
  if (ko) return ko[0].replace(/\s*\.$/, '.');
  return '';
}

function marketSourceItems(ctx = {}, title = '', row = {}) {
  const out = [];
  const seen = new Set();
  const add = (src) => {
    const obj = typeof src === 'object' && src !== null ? src : {title:src};
    const t = decodeMarketText(obj.title || obj.headline || obj.label || '');
    if (!t || t === title || seen.has(t)) return;
    seen.add(t); out.push(obj);
  };
  (ctx.sources || ctx.narrative_sources || []).forEach(add);
  (ctx.matched_issues || []).forEach((m) => (m.sources || m.narrative_sources || []).forEach(add));
  if (out.length) return out.slice(0, 5);
  const narrative = decodeMarketText(ctx.narrative || '');
  if (narrative.includes(' / ')) narrative.split(' / ').forEach((part) => add({title:part.replace(/^[-–•\s]+/, '')}));
  if ((ctx.title || ctx.headline) && !(ctx.label || ctx.theme)) add({title:ctx.title || ctx.headline});
  (ctx.matched_issues || []).forEach((m) => add({title:m.title || m.headline || m.label}));
  return out.slice(0, 5);
}

function marketSourceTitles(ctx = {}, title = '') {
  const out = [];
  const add = (v) => { const t = decodeMarketText(v).trim(); if (t && t !== title && !out.includes(t)) out.push(t); };
  const addSources = (sources = []) => sources.forEach((a) => add(a.title || a.headline || a.label || a));
  addSources(ctx.sources || ctx.narrative_sources || []);
  (ctx.matched_issues || []).forEach((m) => addSources(m.sources || m.narrative_sources || []));
  if (out.length) return out.slice(0, 5);
  const narrative = decodeMarketText(ctx.narrative || '');
  if (narrative.includes(' / ')) narrative.split(' / ').forEach((part) => add(part.replace(/^[-–•\s]+/, '')));
  if ((ctx.title || ctx.headline) && !(ctx.label || ctx.theme)) add(ctx.title || ctx.headline);
  // Only fall back to issue labels when no article/source titles exist.
  (ctx.matched_issues || []).forEach((m) => add(m.title || m.headline || m.label));
  return out.slice(0, 5);
}
function marketEvidenceBlock(row, marketText) {
  const ctx = row.market_issue_context || row.market_context || {};
  const matches = (ctx.matches || ctx.related_items || ctx.news || ctx.items || []).slice(0, 5);
  const listRows = matches.length ? matches : (ctx.label || ctx.summary ? [ctx] : []);
  return `<div class="market-evidence-block">
    ${evidencePills([
      {label:'이슈 영향도', value:fmt(ctx.impact_score), kind:Number(ctx.impact_score || 0) > 0 ? 'warn' : 'neutral'},
      {label:'점수 보정', value:`+${fmt(ctx.score_boost ?? ctx.context_score_boost ?? row.validation_basis?.market_context_score_boost ?? 0)}`, kind:'neutral'},
      {label:'위험', value:marketRiskLabel(ctx.risk || ctx.gap_chase_risk || 'normal'), kind:String(ctx.risk || ctx.gap_chase_risk || '').includes('high') ? 'bad' : 'neutral'},
      ...(marketPolicyLabel(ctx) ? [{label:'용도', value:marketPolicyLabel(ctx), kind:'neutral'}] : [])
    ])}
    ${!listRows.length ? `<p class="evidence-readable-line">${marketNarrativeText(ctx, marketText)}</p>${marketMetricLine(ctx) ? `<small class="market-metric-line">${marketMetricLine(ctx)}</small>` : ''}` : ''}
    ${listRows.length ? `<ul class="market-issue-list audit-quality-list" aria-label="관련 기사 및 이슈">
      <li class="market-issue-header"><b>항목</b><span>상태</span><em>설명</em></li>
      ${listRows.map((item) => {
        const v = marketSentimentView(item);
        const title = item.label || item.theme || item.title || item.headline || '시장 이슈';
        const summary = cleanMarketNarrative(item, title, item.summary || item.risk_note || '추가 설명 없음');
        const metric = marketMetricFromNarrative(item) || marketMetricLine(item) || '';
        const sourceItems = marketSourceItems(item, title, row);
        const scope = marketScopeLabel(item, row);
        const lines = [`<span class="market-issue-summary">${summary}</span>`];
        if (metric) lines.push(`<span class="market-issue-metric">${metric}</span>`);
        sourceItems.forEach((src) => lines.push(`<span class="market-news-title">${marketSourceLine(src, row)}</span>`));
        return `<li class="${v.kind}"><b>${title}<small>${scope}</small></b><span class="${v.kind}">${v.label}</span><em class="market-issue-desc">${lines.join('')}</em></li>`;
      }).join('')}
    </ul>` : ''}
  </div>`;
}

function targetAdjustmentText(row) {
  const p = row.presentation?.target_adjustment_summary;
  if (p?.plain) return p.plain;
  const base = row.target_return_adjustment;
  const vb = row.validation_basis || {};
  const count = vb.target_adjustment_count || 0;
  const applied = vb.target_adjustment_applied_count || 0;
  const rejected = vb.target_adjustment_rejected_count || 0;
  const provisional = vb.target_adjustment_provisional_count || 0;
  const baseText = base ? `장기 목표수익률 ${base.adjustment_pct_points}%p 선반영 · 개선게이트 검증 중` : '장기 목표수익률 기본 보정 없음';
  if (!count) return baseText;
  if (applied) return `${baseText} · 추가 보정 적용 ${applied}/${count}건 · 표본대기 ${provisional}건`;
  return `${baseText} · 추가 보정 미적용 ${count}건 · 거절 ${rejected}건 · 표본대기 ${provisional}건`;
}
function longTargetCell(row, n, label) {
  const adjusted = row[`target_${n}`];
  const adjustedPct = row[`upside_${n}_pct`];
  const original = row[`original_target_${n}`];
  const originalPct = row[`original_upside_${n}_pct`];
  const targetNames = label === '검토' ? {1:'검토 하단',2:'검토 중심',3:'검토 상단'} : {1:'보수 목표가',2:'중심 목표가',3:'상단 목표가'};
  const scenario = label === '검토' ? {1:'변동성 하단',2:'중심 기준',3:'상단 기준'} : {1:'낮은 목표선',2:'전략 평균선',3:'낙관 시나리오'};
  const title = targetNames[n] || `${label}${n}`;
  if (original && Number(original) !== Number(adjusted)) {
    return `<span class="target long adjusted-target"><i>${title}</i><b>${fmt(adjusted)}</b><em>${scenario[n]} · 보정 ${pct(adjustedPct)} · 원 ${fmt(original)}(${pct(originalPct)})</em></span>`;
  }
  return `<span class="target long"><i>${title}</i><b>${fmt(adjusted)}</b><em>${scenario[n]} · ${pct(adjustedPct)}</em></span>`;
}


function targetAdjustmentClass(row) {
  const vb = row.validation_basis || {};
  if ((vb.target_adjustment_applied_count || 0) > 0) return 'positive';
  if ((vb.target_adjustment_count || 0) > 0) return 'caution';
  return 'neutral muted';
}

function targetAdjustmentDetails(row) {
  const items = row.validation_basis?.target_adjustments || [];
  if (!items.length) return '목표가 보정 제안 없음';
  return `<ul>${items.slice(0,4).map((x)=>`<li><b>${x.acceptance_status || (x.accepted ? 'accepted' : 'rejected')}</b> · scale ${fmt(x.target_scale)} · 적용 ${x.applied ? 'yes' : 'no'} · ${x.acceptance_reason || x.reason || ''}${x.samples_needed_for_acceptance !== undefined && x.samples_needed_for_acceptance !== null ? ` · 필요샘플 +${x.samples_needed_for_acceptance}` : ''}</li>`).join('')}</ul>`;
}


function committeeSummaryBlock(row) {
  const committee = row.investment_committee || {};
  const syn = committee.synthesis || {};
  const opinions = committee.opinions || [];
  if (!syn.decision && !opinions.length) return '';
  const label = committeeDecisionLabel(syn.decision);
  const chips = opinions.map((o) => `<span class="committee-chip ${o.opinion || ''}">${o.label || o.agent}: ${o.opinion || '-'}</span>`).join('');
  return `<div class="committee-summary ${syn.decision || ''}"><div class="committee-main"><span>투자성향 위원회</span><b>${label}</b><em>${syn.summary || ''}</em></div><div class="committee-chips">${chips}</div></div>`;
}

function orgSeverityLabel(sev) {
  return ({urgent:'긴급 조치', action:'조치 필요', watch:'관찰', info:'정보'}[sev] || sev || '-');
}
function lifecycleGateLabel(gate) {
  return ({
    validation_coverage: '검증 누적 중',
    no_trade_eligible_recommendations: '품질 게이트 차단',
    no_qualified_active_promotions: '승격 보류',
    committee_zero_approval: '위원회 승인 대기',
  }[gate] || gate || '-');
}
function orgSeverityClass(sev) {
  return sev === 'urgent' ? 'bad' : (sev === 'action' ? 'bad' : (sev === 'watch' ? 'neutral' : 'good'));
}
function guardianPayloadFromPipeline(pipe) {
  const step = (pipe.steps || []).find((s) => String(s.agent || '').includes('org_improvement_guardian'));
  const outputs = step?.outputs || {};
  return outputs['/tmp/org_improvement_guardian_latest.json'] || outputs.org_improvement_guardian || outputs.payload || null;
}

function marketPerformanceSummary(rows, pipe = {}) {
  const pairs = (rows || []).map((row) => {
    const vb = row.validation_basis || {};
    const symbolReturn = Number(vb.symbol_20d_return_pct);
    const benchmarkReturn = Number(vb.benchmark_20d_return_pct);
    if (!Number.isFinite(symbolReturn) || !Number.isFinite(benchmarkReturn)) return null;
    return { symbolReturn, benchmarkReturn };
  }).filter(Boolean);
  if (!pairs.length) return '';
  const avgSymbol = pairs.reduce((sum, x) => sum + x.symbolReturn, 0) / pairs.length;
  const avgBenchmark = pairs.reduce((sum, x) => sum + x.benchmarkReturn, 0) / pairs.length;
  const spread = avgSymbol - avgBenchmark;
  const shock = pipe.market_shock_summary?.summary || {};
  const shockLabel = Number.isFinite(Number(shock.crash_count)) || Number.isFinite(Number(shock.surge_count))
    ? ` / 당일 급락 ${shock.crash_count ?? 0}·급등 ${shock.surge_count ?? 0}`
    : '';
  return ` · 20거래일 기준: 후보 벤치마크 평균 ${pct(avgBenchmark)} / 추천후보 평균 ${pct(avgSymbol)} / 차이 ${pct(spread)}${shockLabel}`;
}

function recommendationSummaryNote(marketLabel, recRunAt, benchmarkLabel, recSourceLabel, performanceLabel = '') {
  const ts = recRunAt ? new Date(recRunAt).toLocaleString() : '-';
  return `<p class="data-note recommendation-context-note">${marketLabel} · ${ts} · ${benchmarkLabel}${performanceLabel}${recSourceLabel} · paper-only 검증용입니다. 실제 주문 권한은 없습니다.</p>`;
}


function priceDateLabel(row) {
  return row.latest_price_date ? `기준일 ${row.latest_price_date}` : '';
}

function recommendationBucket(row) {
  if (row.trade_eligible || row.recommendation_bucket === 'approved') return 'approved';
  if (row.recommendation_bucket === 'research_watch') return 'research_watch';
  if (row.recommendation_bucket === 'rejected') return 'rejected';
  return 'watch';
}

function recommendationSortKey(row) {
  const bucketWeight = recommendationBucket(row) === 'approved' ? 3 : (recommendationBucket(row) === 'research_watch' ? 2 : (recommendationBucket(row) === 'watch' ? 1 : 0));
  return [bucketWeight, Number(row.score || 0), Number(row.weighted_strategy_consensus || row.strategy_consensus || 0), String(row.symbol || '')];
}

function compareRecommendations(a, b) {
  const ak = recommendationSortKey(a);
  const bk = recommendationSortKey(b);
  for (let i = 0; i < ak.length; i += 1) {
    if (typeof ak[i] === 'string') return ak[i].localeCompare(bk[i]);
    if (ak[i] !== bk[i]) return bk[i] - ak[i];
  }
  return 0;
}

function sortRecommendations(rows) {
  return [...(rows || [])].sort(compareRecommendations);
}

function toggleRecDetail(id){
  const el=document.getElementById(id); if(!el) return;
  el.hidden=!el.hidden;
  const btn = document.querySelector(`[data-detail-target="${id}"]`);
  if (btn) {
    btn.setAttribute('aria-expanded', String(!el.hidden));
    const label = btn.dataset.label || btn.textContent.replace(/^접기 · /,'').replace(/^펼치기 · /,'');
    btn.dataset.label = label;
    btn.textContent = `${el.hidden ? '펼치기' : '접기'} · ${label}`;
  }
}
function renderRecommendationBucket(title, subtitle, rows, emptyText) {
  if (!rows.length) return '';
  return `<section class="recommendation-bucket card-list-bucket"><div class="recommendation-section-head"><div><h3>${title}</h3><p>${subtitle}</p></div><span>${rows.length}개</span></div><div class="recommendation-card-list rec-card-grid">${rows.map((row,idx)=>renderRecommendationCard(row,idx)).join('')}</div></section>`;
}

function rejectedCandidateReason(row) {
  const gate = row.trade_gate || {};
  const committee = row.investment_committee?.synthesis || row.committee?.synthesis || {};
  const riskGate = committee.risk_gate || {};
  const critic = row.critic || {};
  const parts = [
    gate.reason === 'committee_reject' ? '위원회 reject' : gate.reason,
    riskGate.decision ? `RiskGate ${riskGate.decision}${riskGate.score !== undefined ? `(${fmt(riskGate.score)})` : ''}` : null,
    critic.summary,
  ].filter(Boolean);
  return parts.join(' · ') || (row.risk_notes || []).slice(0, 1).join('') || '보류 사유 확인 필요';
}

function renderHighScoreRejectedCallout(rows) {
  const blocked = sortRecommendations(rows)
    .filter((row) => recommendationBucket(row) === 'rejected' && Number(row.score || 0) >= 50)
    .slice(0, 3);
  if (!blocked.length) return '';
  return `<section class="high-score-blocked-panel">
    <div class="recommendation-section-head">
      <div><h3>고점수 보류 후보</h3><p>점수는 높지만 위원회/risk gate가 막은 종목입니다. 추천에서 사라진 것이 아니라 보류 섹션으로 이동한 케이스입니다.</p></div>
      <span>${blocked.length}개</span>
    </div>
    <div class="high-score-blocked-list">
      ${blocked.map((row) => `<article class="high-score-blocked-item">
        <div><strong>${companyNameOf(row)}</strong><code>${row.symbol || '-'}</code></div>
        <b>${fmt(row.score)}</b>
        <span>${rejectedCandidateReason(row)}</span>
      </article>`).join('')}
    </div>
  </section>`;
}

function renderRecommendationSections(targetId, rows) {
  const target = document.getElementById(targetId);
  if (!target) return;
  rows = sortRecommendations(rows);
  if (!rows.length) { target.innerHTML = '<div class="empty-state">No data yet.</div>'; return; }
  const buckets = {approved:[], research_watch:[], watch:[], rejected:[]};
  rows.forEach((row)=>{ const k=recommendationBucket(row); (buckets[k] || buckets.watch).push(row); });
  const bucketCounts = Object.fromEntries(Object.entries(buckets).map(([k,v])=>[k,v.length]));
  target.innerHTML = `<div class="recommendation-card-surface"><div class="recommendation-section-head rec-card-surface-head"><div><h3>추천 후보</h3><p>Fund consensus 기준 후보입니다. 가격 계획과 gate만 먼저 표시하고 세부 evidence는 접어둡니다.</p></div><span>${rows.length}개</span></div><div class="rec-filter-summary"><span>매수 ${bucketCounts.approved||0}</span><span>검증대기 ${bucketCounts.research_watch||0}</span><span>관찰 ${bucketCounts.watch||0}</span><span>보류 ${bucketCounts.rejected||0}</span></div>${renderHighScoreRejectedCallout(rows)}${renderRecommendationBucket('매수 후보','가격계획과 risk gate를 먼저 확인하세요.',buckets.approved)}${renderRecommendationBucket('검증대기','추가 확인/샘플이 필요한 후보입니다.',buckets.research_watch)}${renderRecommendationBucket('관찰','Fund consensus는 있으나 paper-buy 승인은 아직 보류된 후보입니다.',buckets.watch)}${renderRecommendationBucket('제외/보류','근거가 약하거나 gate에서 밀린 후보입니다.',buckets.rejected)}</div>`;
}


function shadowToResearchWatch(row) {
  const entry = row.entry || row.last_price || 0;
  const target = row.target || row.target_1 || 0;
  const stop = row.stop || row.stop_reference || 0;
  return {
    symbol: row.symbol,
    action: 'watch',
    action_label: '연구 관찰',
    recommendation_bucket: 'watch',
    recommendation_bucket_label: 'Shadow 관찰',
    score: row.shadow_score || row.raw_signal_score || 0,
    last_price: entry,
    target_1: target, target_2: target, target_3: target, stop_reference: stop,
    upside_1_pct: entry && target ? ((target / entry - 1) * 100) : null,
    upside_2_pct: entry && target ? ((target / entry - 1) * 100) : null,
    upside_3_pct: entry && target ? ((target / entry - 1) * 100) : null,
    downside_stop_pct: entry && stop ? ((stop / entry - 1) * 100) : null,
    strategy_consensus: 1,
    best_logic: row.logic,
    best_logic_label: strategyKoreanLabel(row.logic),
    expected_period: 'shadow/research 관찰',
    confidence_grade: {label:'검증 전'},
    recommendation_reason: `${strategyKoreanLabel(row.logic)} shadow 신호입니다. active 추천이 아니라 후보 전략 forward 관찰용입니다.`,
    validation_basis: {avg_active_excess_return_pct: null, avg_excess_win_rate_pct: null, positive_symbol_edge_count: 0, symbol_validation_sample_count: null},
    reasons: row.reasons || [],
    risk_notes: ['정규 추천이 아닙니다. discovery 전략이 충분한 lifecycle 검증을 통과하기 전까지 research watchlist로만 봅니다.'],
    technical_risk_context: row.technical_risk_context || {},
    caveat: 'paper-only shadow signal',
  };
}

function promotedToRecommendation(row) {
  const test = row.out_of_sample_test || {};
  const train = row.selected_train || {};
  return {
    symbol: row.symbol,
    action: 'candidate_buy_zone',
    action_label: '연구 승격 후보',
    score: Math.max(0, Math.round(((test.total_return_pct || 0) - (test.buy_hold_return_pct || 0)) * 10) / 10),
    strategy_consensus: 1,
    best_logic: train.strategy,
    best_logic_label: strategyKoreanLabel(train.strategy),
    recommendation_reason: `walk-forward 검증에서 ${row.cutoff} 이후 out-of-sample 수익률 ${pct(test.total_return_pct)}, 벤치마크/보유 대비 ${pct((test.total_return_pct || 0) - (test.buy_hold_return_pct || 0))} 초과로 승격된 연구 후보입니다.`,
    caveat: 'historical research promoted 후보이며 실거래 주문이 아닙니다.',
    validation_basis: {
      avg_active_excess_return_pct: (test.total_return_pct || 0) - (test.buy_hold_return_pct || 0),
      avg_excess_win_rate_pct: test.win_rate_pct,
      positive_symbol_edge_count: 1,
      symbol_validation_sample_count: test.trade_count,
    },
    reasons: [
      `테스트 수익률 ${pct(test.total_return_pct)} vs buy-hold ${pct(test.buy_hold_return_pct)}`,
      `최대낙폭 ${pct(test.max_drawdown_pct)}, profit factor ${fmt(test.profit_factor)}`,
      `훈련 구간 ${train.bars || '-'} bars / 테스트 구간 ${test.bars || '-'} bars`,
    ],
    risk_notes: ['과거 walk-forward 결과이며 현재가 기반 매수 신호와는 별도입니다. 추가 검증 후 판단하세요.'],
    disclosure_risk: row.disclosure_features || {},
    horizon_days: test.bars,
    market_20d_return_pct: test.buy_hold_return_pct,
  };
}



function reviewOnlyTargetLevels(last, trend={}) {
  const px = Number(last || 0);
  if (!px) return {target_1:null,target_2:null,target_3:null,stop_reference:null,upside_1_pct:null,upside_2_pct:null,upside_3_pct:null,downside_stop_pct:null};
  const vol = Math.max(3, Math.min(12, Number(trend.volatility_20d_pct || trend.atr_pct || 6)));
  const up1 = Math.max(3, Math.min(7, vol * 0.8));
  const up2 = Math.max(6, Math.min(12, vol * 1.4));
  const up3 = Math.max(10, Math.min(18, vol * 2.0));
  const stop = Math.max(4, Math.min(10, vol * 1.0));
  return {
    target_1: px * (1 + up1 / 100),
    target_2: px * (1 + up2 / 100),
    target_3: px * (1 + up3 / 100),
    stop_reference: px * (1 - stop / 100),
    upside_1_pct: up1,
    upside_2_pct: up2,
    upside_3_pct: up3,
    downside_stop_pct: -stop,
  };
}

function withReviewOnlyTargets(row, trend={}) {
  if (!row) return row;
  if (row.target_1 && row.target_2 && row.stop_reference) return row;
  const levels = reviewOnlyTargetLevels(row.last_price, trend);
  if (!levels.target_1) return row;
  return {
    ...row,
    _review_only_targets: true,
    target_1: row.target_1 ?? levels.target_1,
    target_2: row.target_2 ?? levels.target_2,
    target_3: row.target_3 ?? levels.target_3,
    stop_reference: row.stop_reference ?? levels.stop_reference,
    upside_1_pct: row.upside_1_pct ?? levels.upside_1_pct,
    upside_2_pct: row.upside_2_pct ?? levels.upside_2_pct,
    upside_3_pct: row.upside_3_pct ?? levels.upside_3_pct,
    downside_stop_pct: row.downside_stop_pct ?? levels.downside_stop_pct,
  };
}

function symbolReviewToRecommendationRow(review) {
  const ev = review.active_evaluation || {};
  const decision = symbolDecision(review);
  const trend = review.trend || {};
  const validation = review.validation || {};
  const sampleCount = validation.samples ?? review.validation_samples ?? 0;
  const last = trend.last_price ?? review.last_price;
  const reason = ev.recommendation_reason || decision.reason || review.summary || '상세검토 이력 기반 검증 결과입니다.';
  const fallbackTargets = reviewOnlyTargetLevels(last, trend);
  return {
    symbol: review.symbol,
    name: review.name,
    action: decision.buy_opinion ? 'buy' : 'watch',
    action_label: decision.label || ev.action_label || ev.action || '검토 결과',
    recommendation_bucket: decision.buy_opinion ? 'research_watch' : 'watch',
    recommendation_bucket_label: decision.buy_opinion ? '검토 후보' : '검토 관찰',
    trade_eligible: false,
    score: Number(ev.score ?? review.active_eval_score ?? (decision.buy_opinion ? 60 : 0)),
    confidence_grade: decision.confidence || 'review',
    best_logic: ev.logic || ev.best_logic || review.analysis_source || 'symbol_review',
    best_logic_label: ev.logic ? strategyKoreanLabel(ev.logic) : '종목 상세검토',
    expected_period: `${ev.horizon_days || 20}거래일 검토`,
    horizon_days: ev.horizon_days || 20,
    last_price: last,
    _review_only_targets: !(ev.target_1 || ev.target_price),
    target_1: ev.target_1 ?? ev.target_price ?? fallbackTargets.target_1,
    target_2: ev.target_2 ?? fallbackTargets.target_2,
    target_3: ev.target_3 ?? fallbackTargets.target_3,
    stop_reference: ev.stop_reference ?? ev.stop_price ?? fallbackTargets.stop_reference,
    upside_1_pct: ev.upside_1_pct ?? fallbackTargets.upside_1_pct,
    upside_2_pct: ev.upside_2_pct ?? fallbackTargets.upside_2_pct,
    upside_3_pct: ev.upside_3_pct ?? fallbackTargets.upside_3_pct,
    downside_stop_pct: ev.downside_stop_pct ?? fallbackTargets.downside_stop_pct,
    validation_basis: {
      avg_active_excess_return_pct: validation.avg_excess_return_pct ?? review.avg_excess_return_pct,
      avg_excess_win_rate_pct: validation.success_rate_pct,
      positive_symbol_edge_count: Object.keys(validation.action_counts || review.action_counts || {}).length,
      symbol_validation_sample_count: sampleCount,
    },
    strategy_consensus: review.active_strategy_count ?? '-',
    weighted_strategy_consensus: review.active_strategy_count ?? 0,
    recommendation_reason: reason,
    reasons: [reason, `검토 표본 ${sampleCount}건 · 시장 대비 성과 ${pct(validation.avg_excess_return_pct ?? review.avg_excess_return_pct)}`, `20일 ${pct(trend.r20_pct)} · 60일 ${pct(trend.r60_pct)}`].filter(Boolean),
    risk_notes: (review.corporate_action_risk?.flagged ? ['기업행위/거래정지 리스크 감지'] : []).concat(decision.checklist || []),
    critic: {summary: decision.reason || '-'},
    portfolio_risk: {notes: decision.checklist || []},
    investment_committee: ev.investment_committee,
    regime_gate: ev.regime_gate,
    disclosure_risk: review.disclosure_features || {},
    market_20d_return_pct: trend.r20_pct,
  };
}


function latestRecommendationForSymbol(symbol) {
  if (!symbol) return null;
  const key = String(symbol).toUpperCase();
  return latestRecommendationBySymbol.get(key) || latestRecommendationBySymbol.get(String(symbol)) || null;
}

function mergeReviewIntoLatestRecommendation(review) {
  const latest = latestRecommendationForSymbol(review?.symbol);
  if (!latest) return symbolReviewToRecommendationRow(review);
  const validation = review.validation || {};
  const sampleCount = validation.samples ?? review.validation_samples ?? 0;
  const decision = symbolDecision(review);
  const notes = [
    `상세검토 연결: ${decision.label || review.recommendation_hint || review.status || '검토'} · 샘플 ${sampleCount}건 · 평균 초과수익 ${pct(validation.avg_excess_return_pct ?? review.avg_excess_return_pct)}`,
    ...(decision.checklist || []).slice(0, 3),
  ].filter(Boolean);
  return withReviewOnlyTargets({
    ...latest,
    _symbol_review_attached: true,
    _symbol_review_label: decision.label || review.recommendation_hint || review.status || '상세검토',
    _symbol_review_run_at: review.run_at,
    risk_notes: [...(latest.risk_notes || []), ...notes],
    presentation: {
      ...(latest.presentation || {}),
      next_checks: [...((latest.presentation || {}).next_checks || []), ...notes].slice(0, 6),
    },
  }, review.trend || {});
}

function latestRecommendationNotice(symbol) {
  const latest = latestRecommendationForSymbol(symbol);
  if (!latest) return '<div class="summary-risk neutral muted"><b>추천현황</b><span>현재 최신 추천현황에는 없는 종목입니다. 아래 요약은 universe/상세검토 이력 기준입니다.</span></div>';
  return `<div class="summary-risk positive"><b>추천현황 최신 연결</b><span>${latest.recommendation_bucket_label || latest.action_label || latest.action || '관찰'} · 점수 ${fmt(latest.score)} · ${latest.best_logic_label || latest.best_logic || latest.logic || '-'}</span></div>`;
}

function renderSymbolReviewDetail(review, sourceLabel = '상세검토') {
  if (!review?.symbol) return;
  selectedUniverseSymbol = review.symbol;
  const input = document.getElementById('symbol-review-input');
  if (input) input.value = review.symbol;
  const decision = symbolDecision(review);
  setHtml('symbol-review-summary', `${review.name || review.symbol} · ${sourceLabel} · ${decision.label} · ${decision.buy_opinion ? '매수 의견 있음' : '매수 의견 아님'} · 검증 ${(review.validation?.samples ?? review.validation_samples ?? 0)}건`);
  const rec = mergeReviewIntoLatestRecommendation(review);
  const html = `
    <div class="symbol-review-drawer-head">
      <div><p class="eyebrow">Review as Recommendation Card</p><h3>${review.name || review.symbol} 검토 결과</h3><p>최신 추천현황이 있으면 그 카드 기준으로, 없으면 상세검토 결과를 추천 카드 형식으로 표시합니다.</p></div>
      <div class="symbol-review-drawer-actions"><span class="tag">${sourceLabel}</span><button type="button" class="symbol-review-drawer-close" data-close-symbol-review>접기</button></div>
    </div>
    <div class="symbol-review-drawer-body">
      <div class="symbol-review-rec-detail recommendation-card-list">
        ${renderRecommendationCard(rec, 0)}
        <article class="audit-card ${review.recommendation_hint || ''} ${decision.grade || ''}">
          <div class="audit-card-top"><strong>검토 원본 요약</strong>${decisionBadge(decision)}</div>
          <div class="audit-sub">${review.market || '-'} · ${review.in_universe ? 'universe 포함' : 'universe 미포함'} · ${review.analysis_source || '-'}</div>
          ${latestRecommendationNotice(review.symbol)}
          ${decisionPanel(review)}
          <div class="strategy-metrics">
            <div><span>가격 데이터</span><b>${review.trend?.bars ?? review.bars ?? '-'}</b></div>
            <div><span>최근 종가</span><b>${fmt(review.trend?.last_price ?? review.last_price)}</b></div>
            <div><span>20일</span><b>${pct(review.trend?.r20_pct)}</b></div>
            <div><span>60일</span><b>${pct(review.trend?.r60_pct)}</b></div>
            <div><span>검토 표본</span><b>${review.validation?.samples ?? review.validation_samples ?? 0}</b></div>
            <div><span>평균 초과수익</span><b>${pct(review.validation?.avg_excess_return_pct ?? review.avg_excess_return_pct)}</b></div>
          </div>
          ${renderSelectedHistoryContext(review.symbol)}
        </article>
      </div>
    </div>`;
  const drawer = document.getElementById('symbol-review-drawer');
  if (drawer) {
    drawer.innerHTML = html;
    drawer.classList.add('is-open');
    drawer.scrollIntoView({behavior:'smooth', block:'start'});
  } else {
    setHtml('symbol-review-result', html);
  }
}


function optimizerTargetCells(row) {
  const tp = row.target_policy || {};
  const hint = tp.short_horizon_hint || {};
  const applied = tp.applied && tp.adjusted_target;
  const proposed = tp.target_return_adjustment_pct_points != null;
  if (applied) {
    const up = row.last_price ? ((Number(tp.adjusted_target) / Number(row.last_price) - 1) * 100) : null;
    return `
              <span class="target short"><i>개선게이트 적용</i><b>${fmt(tp.adjusted_target)}</b><em>${pct(up)}</em></span>
              <span class="target short"><i>근거</i><b>${hint.horizon_days || '-'}D</b><em>${pct(hint.hit_pct)}</em></span>
              <span class="target short"><i>보정폭</i><b>-${fmt(tp.target_return_adjustment_pct_points)}%p</b><em>${tp.acceptance_status || 'accepted'}</em></span>`;
  }
  if (proposed) {
    return `
              <span class="target short muted"><i>개선게이트 후보</i><b>-${fmt(tp.target_return_adjustment_pct_points)}%p</b><em>${tp.acceptance_status || '검증중'}</em></span>
              <span class="target short muted"><i>근거</i><b>${hint.horizon_days || '-'}D</b><em>${pct(hint.hit_pct)}</em></span>
              <span class="target short muted"><i>판정</i><b>${tp.accepted ? '수락' : '미적용'}</b><em>${tp.acceptance_reason || 'auditor 대기'}</em></span>`;
  }
  return `
              <span class="target short muted"><i>개선게이트</i><b>미적용</b><em>고정보정 없음</em></span>
              <span class="target short muted"><i>보정근거</i><b>관찰</b><em>2D/5D</em></span>
              <span class="target short muted"><i>적용조건</i><b>EV 통과</b><em>auditor</em></span>`;
}

function shortHorizonMetrics(row) {
  const p = row.short_horizon_profile || row.validation_basis?.short_horizon_profile || {};
  if (!p.sample_count && !p.samples) return null;
  const h2 = p.by_horizon?.['2'] || p;
  const h5 = p.by_horizon?.['5'] || null;
  const v2 = h2.target_minus_2_pct_points_hit_pct ?? h2.target_under_2_pct_hit_pct ?? h2.target_or_under_2pct_pct;
  const v5 = h5 ? (h5.target_minus_2_pct_points_hit_pct ?? h5.target_under_2_pct_hit_pct ?? h5.target_or_under_2pct_pct) : null;
  const profile = h5?.profile || p.profile;
  const label = profile === 'strong_adjusted_target_touch' ? '강함' : (profile === 'watch_adjusted_target_touch' ? '관찰' : '약함');
  return {p,h2,h5,v2,v5,label};
}

function shortHorizonText(row) {
  const m = shortHorizonMetrics(row);
  if (!m) return ;
  const horizonText = m.h5 ? `2D ${pct(m.v2)} · 5D ${pct(m.v5)}` : `2D ${pct(m.v2)}`;
  return `<div class="summary-risk positive compact-signal"><b>보정관찰</b><span>${m.label} · 목표수익률-2%p 근접 ${horizonText} · 평균최대 ${pct((m.h5 || m.h2).avg_max_up_pct)} · 표본 ${(m.h5 || m.h2).sample_count || m.p.sample_count || m.p.samples}</span></div>`;
}

function shortHorizonStat(row) {
  const m = shortHorizonMetrics(row);
  if (!m) return compactStat('보정관찰', '-', '2D/5D 없음');
  const main = m.h5 ? `${pct(m.v2)} / ${pct(m.v5)}` : pct(m.v2);
  return compactStat('보정관찰', main, m.h5 ? '2D / 5D' : '2D');
}

function recommendationSourceText(row = {}) {
  const source = row.recommendation_source_model || '';
  const targetSource = row.target_price_source || '';
  const isFund = source.includes('fund') || targetSource.includes('fund');
  if (isFund && targetSource.includes('invalidated')) return 'Fund 선정 · 가격합의 무효화';
  if (isFund) return 'Fund 선정 · 가격합의 반영';
  return source ? source.replaceAll('_', ' ') : '선정 근거 확인';
}

function renderRecommendationCard(row, idx) {
  try {
    const criticIssues = row.critic?.issues?.length ? row.critic.issues : [row.critic?.summary || '뚜렷한 반대 근거는 제한적입니다'];
    const portfolioNotes = row.portfolio_risk?.notes?.length ? row.portfolio_risk.notes : ['특이사항 낮음'];
    const reasonParts = recommendationReasonParts(row);
    const coreReasons = reasonParts.core.length ? reasonParts.core : (row.reasons || []);
    const checkReasons = reasonParts.checks.length ? reasonParts.checks : criticIssues;
    const human = humanDecisionSummary(row);
    const present = row.presentation || {};
    const whyBullets = hideRawTrustMetricText(summaryBullets(human.why, 5));
    const positives = hideRawTrustMetricText(present.positive_factors || []);
    const blockers = present.primary_blockers || [];
    const nextChecks = hideRawTrustMetricText(present.next_checks || []);
    const committeeText = committeeCompactText(human.committeeView);
    const committeeParts = committeeSplit(row, human.committeeView);
    const symbol = row.symbol || '-';
    const cname = companyNameOf(row);
    const market = row.market || marketOf(symbol) || '-';
    const targetLabel = row._review_only_targets ? '검토' : '장기';
    const targetNote = row._review_only_targets ? '<div class="summary-risk neutral muted"><b>목표가 성격</b><span>추천/매수 목표가가 아니라 현재가와 변동성 기반의 검토용 기준가입니다.</span></div>' : '';
    const detailId = `rec-detail-${String(symbol).replace(/[^a-zA-Z0-9_-]/g,'_')}-${idx}`;
    const primaryReason = hideRawTrustMetricText(plainSummaryBullets(row, human)).slice(0, 1).join('') || human.headline || '';
    const recLabel = row.recommendation_bucket_label || row.action_label || row.action || '-';
    const vb = row.validation_basis || {};
    const fund = vb.fund_consensus || {};
    const marketText = row.market_issue_context
      ? `${row.market_issue_context.label || '동적 시장 이슈'} · impact ${fmt(row.market_issue_context.impact_score)} · ${row.market_issue_context.risk || 'normal'}`
      : (row.market_context ? `${row.market_context.label || row.market_context.theme || '시장 컨텍스트'} · impact ${fmt(row.market_context.impact_score)}` : '시장 evidence 없음');
    return `
      <article class="recommendation-card rec-product-card readable-card">
        <div class="rec-product-head">
          <div class="rec-product-title"><strong>${cname}</strong><code>${symbol}</code><span>${market}</span></div>
          <span class="${badgeClass(row.action)}">${recLabel}</span>
        </div>

        <div class="rec-product-core">
          <div class="rec-decision-score"><b>${fmt(row.score)}</b><span>판단점수</span><em>${row.confidence_grade?.label || row.confidence_grade || '검증 기반'}</em></div>
          <div class="rec-decision-copy"><strong>${human.headline || primaryReason}</strong><p>${primaryReason || human.mainRisk || '-'}</p></div>
          <div class="rec-decision-meta compact-source">
            <span><b>기준</b>${recommendationSourceText(row)}</span>
            <span><b>기간</b>${row.expected_period || ((row.horizon_days || 20) + '거래일')}</span>
          </div>
        </div>

        ${pricePlanBlock(row)}

        ${compactEvidenceStatus(row, vb, blockers, human)}

        <button class="rec-detail-toggle rec-evidence-toggle" data-detail-target="${detailId}" aria-expanded="false" onclick="toggleRecDetail('${detailId}')">펼치기 · 상세 근거 / 위원회 / Risk / 시장</button>
        <div id="${detailId}" class="rec-lazy-detail rec-evidence-drawer" hidden>
          <section class="rec-evidence-source internal"><h4>추천엔진 근거</h4>${listBlock('긍정 근거', positives.length ? positives : coreReasons, 'positive')}${listBlock('차단/주의 요인', blockers.length ? blockers : checkReasons, 'caution')}${listBlock('다음 확인', nextChecks.length ? nextChecks : (row.risk_notes || []).concat(portfolioNotes), 'neutral')}</section>
          <section class="rec-evidence-source committee"><h4>위원회 / Risk Gate <small>판단 흐름</small></h4>${committeeEvidenceBlock(row, committeeText)}</section>
          <section class="rec-evidence-source audit"><h4>보조 신호/국면 라벨 <small>성과·위험 해석</small></h4>${auditEvidenceBlock(row, vb)}</section>
          <section class="rec-evidence-source fund"><h4>Fund consensus <small>선정 근거</small></h4>${fundEvidenceBlock(row, vb)}</section>
          <section class="rec-evidence-source supply"><h4>수급/거래주체 evidence <small>보조자료</small></h4>${supplyEvidenceBlock(row, vb)}</section>
          <section class="rec-evidence-source market"><h4>시장/이슈 evidence <small>별도 소스</small></h4>${marketEvidenceBlock(row, marketText)}</section>
        </div>
      </article>`;
  } catch (err) {
    console.error('recommendation card render failed', err, row);
    const symbol = row?.symbol || '-';
    const name = row?.name || symbolNames[symbol] || symbol;
    return `<article class="recommendation-card rec-card-v2 rec-card-simple"><div class="rec-rank">#${idx + 1}</div><div class="rec-v2-main"><div class="rec-v2-title rec-title-simple"><div class="rec-symbol-title force-visible"><strong>${name}</strong><code>${symbol}</code><span>렌더 fallback · ${row?.logic || '-'}</span></div><span class="badge neutral">fallback</span></div><p class="hint">카드 상세 렌더 중 오류가 있어 안전 카드로 표시했습니다.</p></div></article>`;
  }
}

function symbolDecision(r) {
  if (r.decision) return r.decision;
  if (r.corporate_action_risk?.flagged || r.recommendation_hint === 'corporate_action_quarantine') return {label:'매수 금지', grade:'danger', buy_opinion:false, reason:'기업행위/거래정지 리스크가 감지되었습니다.', checklist:['이벤트 해소 후 재검토']};
  const ev = r.active_evaluation || {};
  const score = Number(ev.score || 0);
  if (ev.action === 'candidate_buy_zone') return {label: score >= 70 ? '매수 후보' : '약한 매수 후보', grade: score >= 70 ? 'good' : 'caution', buy_opinion:true, reason:`active 전략 매수 후보 신호 · 점수 ${fmt(score)}`, checklist:['추천 사유 확인','손절 기준 확인','최신 공시 확인']};
  if (r.recommendation_hint === 'historically_supported_watch_candidate') return {label:'관심 관찰 후보', grade:'neutral', buy_opinion:false, reason:'현재 매수 신호는 없지만 과거 검증 성과가 양호합니다.', checklist:['active 신호 대기','관심종목 모니터링']};
  if (r.recommendation_hint === 'weak_historical_edge') return {label:'매수 근거 약함', grade:'danger', buy_opinion:false, reason:'과거 검증 edge가 약합니다.', checklist:['성과 개선 전 보류']};
  return {label:'매수의견 없음', grade:'neutral', buy_opinion:false, reason:'현재 active 매수 신호가 없습니다.', checklist:['active 보조 신호 확인']};
}
function decisionBadge(d){ return `<span class="decision-badge ${d.grade || 'neutral'}">${d.label || '-'}</span>`; }
function decisionPanel(r){ const d=symbolDecision(r); return `<div class="decision-panel ${d.grade || 'neutral'}"><div><span class="decision-kicker">최종 판정</span><strong>${d.label || '-'}</strong><em>${d.buy_opinion ? '매수 의견 있음' : '매수 의견 아님'}</em></div><p>${d.reason || '-'}</p><ul>${(d.checklist||[]).map(x=>`<li>${x}</li>`).join('')}</ul></div>`; }

function symbolHistorySummaryCard(r) {
  const actionCounts = Object.entries(r.action_counts || {}).slice(0, 2).map(([k,v]) => `${k}:${v}`).join(' · ');
  const hint = r.recommendation_hint || r.status || '-';
  return `
    <article class="audit-card ${r.status || ''} history-symbol-card" data-symbol="${r.symbol || ''}" data-review-id="${r.id || ''}" title="클릭하면 이 검토 결과를 추천현황 카드 형태로 봅니다">
      <div class="history-card-head"><strong>${r.name || r.symbol}</strong><span>${r.symbol}</span></div>
      <div class="history-card-meta"><span class="badge neutral">${hint}</span><span>${r.market || '-'}</span></div>
      <div class="history-card-stats"><span>샘플 ${r.validation_samples ?? 0}</span><span>초과 ${pct(r.avg_excess_return_pct)}</span></div>
      ${actionCounts ? `<div class="history-card-foot">${actionCounts}</div>` : ''}
    </article>`;
}

function renderSymbolReviewHistory(rows) {
  symbolReviewHistoryRows = rows || [];
  symbolReviewPayloadById = new Map(symbolReviewHistoryRows.filter((r) => r.id && r.payload).map((r) => [String(r.id), r.payload]));
  const target = document.getElementById('symbol-review-history');
  if (target) renderCards('symbol-review-history', symbolReviewHistoryRows, symbolHistorySummaryCard, 8);
}

function renderSelectedHistoryContext(symbol) {
  const rows = symbolReviewHistoryRows.filter((r) => r.symbol === symbol).slice(0, 5);
  if (!rows.length) return '';
  return `<section class="symbol-history-detail"><h4>최근 검토 이력 맥락</h4><div class="table-wrap"><table class="data-table"><thead><tr><th>시각</th><th>판정</th><th>가격</th><th>샘플</th><th>초과수익</th><th>Action</th></tr></thead><tbody>${rows.map((r) => `<tr class="symbol-review-history-row" data-review-id="${r.id || ''}" data-symbol="${r.symbol || ''}" title="클릭하면 이 검토 결과를 추천현황 카드 형태로 봅니다"><td>${new Date(r.run_at).toLocaleString()}</td><td>${r.recommendation_hint || r.status || '-'}</td><td>${fmt(r.last_price)}</td><td>${r.validation_samples ?? 0}</td><td>${pct(r.avg_excess_return_pct)}</td><td>${Object.entries(r.action_counts || {}).map(([k,v])=>`${k}:${v}`).join('<br>') || '-'}</td></tr>`).join('')}</tbody></table></div></section>`;
}


async function runSymbolReview() {
  const input = document.getElementById('symbol-review-input');
  const symbol = (input?.value || '').trim().toUpperCase();
  if (!symbol) return;
  selectedUniverseSymbol = symbol;
  setHtml('symbol-review-summary', `${symbol} 검토 중입니다...`);
  setHtml('symbol-review-result', '<div class="empty-state">분석 실행 중...</div>');
  try {
    const row = await api(`/api/research/symbol-review?symbol=${encodeURIComponent(symbol)}&runs=3`);
    const hist = await api('/api/research/symbol-review/history?limit=20').catch(() => ({items: []}));
    renderSymbolReviewHistory(hist.items || []);
    if (!document.querySelector('#symbol-review-result .symbol-overview-card')) {
      await renderSymbolOverview(row.symbol || symbol).catch(console.error);
    }
    renderSymbolReviewDetail(row, '방금 실행한 상세검토');
  } catch (err) {
    setHtml('symbol-review-summary', `<b>검토 실패</b>: ${err.message}`);
  }
}

function moneyCompact(value) {
  if (value === null || value === undefined) return '-';
  const n = Number(value);
  if (Number.isNaN(n)) return '-';
  if (Math.abs(n) >= 1_000_000_000_000) return `${fmt(n / 1_000_000_000_000)}조`;
  if (Math.abs(n) >= 100_000_000) return `${fmt(n / 100_000_000)}억`;
  return fmt(n);
}


function renderInlineHistoryReviewRow(r) {
  const id = String(r.id || '');
  if (!id || String(expandedSymbolReviewId || '') !== id) return '';
  const payload = symbolReviewPayloadById.get(id) || r.payload;
  if (!payload) return '';
  const rec = symbolReviewToRecommendationRow(payload);
  const decision = symbolDecision(payload);
  return `<tr class="symbol-review-expanded-row"><td colspan="6">
    <div class="symbol-review-inline-panel recommendation-card-list">
      <div class="symbol-review-inline-head"><div><p class="eyebrow">Review + Latest Recommendation</p><h3>${payload.name || payload.symbol} 검토 결과</h3><p>최신 추천현황 카드에 상세검토 결과를 붙여 표시합니다.</p></div><button type="button" class="symbol-review-drawer-close" data-close-inline-review>접기</button></div>
      ${renderRecommendationCard(mergeReviewIntoLatestRecommendation(payload), 0)}
      <article class="audit-card ${payload.recommendation_hint || ''} ${decision.grade || ''}">
        <div class="audit-card-top"><strong>검토 원본 요약</strong>${decisionBadge(decision)}</div>
        <div class="audit-sub">${payload.market || '-'} · ${payload.in_universe ? 'universe 포함' : 'universe 미포함'} · ${payload.analysis_source || '-'}</div>
        ${!payload.in_universe ? '<div class="rec-action-line"><span>Universe</span><b>지속 모니터링 대상 아님</b><button type="button" class="button secondary" data-add-reviewed-symbol="' + (payload.symbol || '') + '">Watch 편입</button></div>' : ''}
        ${latestRecommendationNotice(payload.symbol)}
        ${decisionPanel(payload)}
      </article>
    </div>
  </td></tr>`;
}

function renderOverviewHistory(rows = []) {
  if (!rows.length) return '<div class="empty-state">검토 이력이 없습니다.<br>상단의 상세검토 버튼으로 새 이력을 만들 수 있습니다.</div>';
  return `<div class="table-wrap"><table class="data-table"><thead><tr><th>시각</th><th>판정</th><th>가격</th><th>샘플</th><th>초과수익</th><th>Action</th></tr></thead><tbody>${rows.map((r) => `
    <tr class="symbol-review-history-row ${String(expandedSymbolReviewId || '') === String(r.id || '') ? 'is-expanded' : ''}" data-review-id="${r.id || ''}" data-symbol="${r.symbol || ''}" title="클릭하면 이 검토 결과가 아래로 펼쳐집니다"><td>${r.run_at ? new Date(r.run_at).toLocaleString() : '-'}</td><td>${r.recommendation_hint || r.status || '-'}</td><td>${fmt(r.last_price)}</td><td>${r.validation_samples ?? 0}</td><td>${pct(r.avg_excess_return_pct)}</td><td>${Object.entries(r.action_counts || {}).map(([k,v])=>`${k}:${v}`).join('<br>') || '-'}</td></tr>
    ${renderInlineHistoryReviewRow(r)}
  `).join('')}</tbody></table></div>`;
}

async function renderSymbolOverview(symbol) {
  if (!symbol) return;
  selectedUniverseSymbol = symbol;
  const input = document.getElementById('symbol-review-input');
  if (input) input.value = symbol;
  setHtml('symbol-review-summary', `${symbol} 요약을 불러오는 중입니다.`);
  setHtml('symbol-review-result', '<div class="empty-state">요약 로딩 중입니다.</div>');
  const overview = await api(`/api/research/symbol-overview?symbol=${encodeURIComponent(symbol)}&history_limit=10`);
  const member = overview.universe_member || {};
  const checks = member.payload?.checks || {};
  const financial = overview.financial_quality || {};
  const valuation = overview.valuation || {};
  const disclosure = overview.disclosure_features || {};
  const price = overview.price_signal || {};
  const recent = overview.recent_disclosures || [];
  const finWarnings = financial.warnings || [];
  const finSupports = financial.supports || [];
  (overview.history || []).forEach((r) => { if (r.id && r.payload) symbolReviewPayloadById.set(String(r.id), r.payload); });
  setHtml('symbol-review-summary', `${overview.name || overview.symbol} · ${statusLabel(member.status) || '미등록'} · 공시 H:${disclosure.high ?? 0} M:${disclosure.medium ?? 0} P:${disclosure.positive ?? 0} · 검토 ${(overview.history || []).length}건`);
  const latestReview = (overview.history || []).find((r) => r.payload)?.payload || null;
  // Keep the symbol-name click as a lightweight overview.  The full recommendation-style
  // card is opened only by the explicit 상세검토 action or an expanded history row;
  // otherwise the same card appeared once in the overview and again in the review drawer.
  setHtml('symbol-review-result', `
    <article class="audit-card symbol-overview-card">
      <div class="audit-card-top"><strong>${overview.name || nameOf(overview.symbol)}</strong><span class="${badgeClass(member.status || 'neutral')}">${statusLabel(member.status) || '미등록'}</span></div>
      <div class="audit-sub">${overview.market === 'KR' ? '한국' : '미국'} · universe 점수 ${fmt(member.score)} · ${member.updated_at ? new Date(member.updated_at).toLocaleString() : '-'}</div>
      <div class="strategy-metrics">
        <div><span>최근 종가</span><b>${fmt(price.latest_close)}</b><em>${price.latest_date || ''}</em></div>
        <div><span>RSI</span><b>${fmt(price.rsi_14)}</b><em>14D</em></div>
        <div><span>20D 변동성</span><b>${pct(price.volatility_20d_pct)}</b></div>
        <div><span>52W 고점대비</span><b>${pct(price.distance_52w_high_pct)}</b></div>
        <div><span>가격 데이터</span><b>${checks.price_bars ?? '-'}</b></div>
        <div><span>공시</span><b>${disclosure.total ?? checks.disclosures ?? 0}</b></div>
      </div>
      <section class="symbol-overview-section"><h4>추천현황 / 상세검토 연결</h4>${latestRecommendationNotice(overview.symbol)}${latestReview ? decisionPanel(latestReview) : '<p class="summary-sentence">아직 저장된 상세검토 결과가 없습니다. 상단 상세검토 버튼으로 최신 검토를 실행할 수 있습니다.</p>'}<p class="summary-sentence muted">전체 추천 카드 형식의 상세 결과는 상단 상세검토 버튼 또는 검토 이력 펼치기에서만 표시합니다.</p></section>
      <section class="symbol-overview-section"><h4>재무 요약</h4>
        <div class="strategy-metrics">
          <div><span>기간</span><b>${financial.latest_period || '-'}</b></div>
          <div><span>매출</span><b>${moneyCompact(financial.revenue)}</b></div>
          <div><span>영업이익률</span><b>${pct(financial.operating_margin_pct)}</b></div>
          <div><span>부채비율</span><b>${pct(financial.debt_ratio_pct)}</b></div>
          <div><span>매출성장</span><b>${pct(financial.revenue_growth_pct)}</b></div>
          <div><span>점수조정</span><b>${fmt(financial.score_adjustment)}</b></div>
        </div>
        <p class="summary-sentence">${finWarnings.length ? `주의: ${finWarnings.join(', ')}` : (finSupports.length ? `지원 근거: ${finSupports.join(', ')}` : '요약 가능한 재무 데이터가 아직 부족합니다.')}</p>
      </section>
      <section class="symbol-overview-section"><h4>밸류에이션</h4>
        <div class="strategy-metrics">
          <div><span>PER</span><b>${fmt(valuation.per)}</b></div>
          <div><span>PBR</span><b>${fmt(valuation.pbr)}</b></div>
          <div><span>ROE</span><b>${pct(valuation.roe_pct)}</b></div>
          <div><span>시가총액</span><b>${moneyCompact(valuation.market_cap)}</b></div>
          <div><span>배당수익률</span><b>${pct(valuation.dividend_yield_pct)}</b></div>
          <div><span>Beta</span><b>${fmt(valuation.beta)}</b></div>
        </div>
        <p class="summary-sentence">${valuation.source === 'yfinance' ? 'PER/PBR 등 시장 지표는 yfinance 기준입니다.' : 'PER/PBR은 제공되지 않았고, ROE는 저장 재무제표 기준으로 계산했습니다.'}</p>
      </section>
      <section class="symbol-overview-section"><h4>최근 공시</h4>
        ${recent.length ? `<ul>${recent.map((d) => `<li><b>${d.risk_level || '-'}</b> ${d.rcept_dt || ''} · ${d.report_nm || '-'}</li>`).join('')}</ul>` : '<div class="empty-state">저장된 최근 공시가 없습니다.</div>'}
      </section>
      <section class="symbol-overview-section"><h4>검토 이력</h4>${renderOverviewHistory(overview.history || [])}</section>
    </article>
  `);
}

function statusLabel(status) {
  return { active: '활성', watch: '관찰', probation: '검증유예', retired: '퇴역', benchmark: '벤치마크', candidate: '후보', pending_validation: '검증대기' }[status] || status || '-';
}

function strategyKoreanLabel(logic) {
  if (logic === 'balanced_range_v1') return '균형형 박스권 돌파';
  if (logic === 'conservative_range_v1') return '보수형 박스권 돌파';
  if (logic === 'rsi_reversion') return 'RSI 과매도 반등';
  const m = /^range_grid_t(\d+)_s(\d+)_m(\d+)_q(\d+)$/.exec(logic || '');
  if (m) {
    const [, target, stop, momentum, quality] = m;
    return `검증형 박스권 전략 · 목표 ${target}% · 손절 ${stop}% · 모멘텀 ${momentum} · 품질 ${quality}`;
  }
  return logic || '-';
}

function strategyDescription(row) {
  const logic = row.logic || '';
  if (logic === 'balanced_range_v1') return '수익 목표와 손절 폭을 균형 있게 둔 기본 박스권/모멘텀 전략입니다.';
  if (logic === 'conservative_range_v1') return '낮은 목표가와 보수적 위험 기준으로 안정성을 우선하는 전략입니다.';
  if (logic === 'rsi_reversion') return 'RSI 과매도 이후 평균회귀 반등을 노리는 전략입니다.';
  if (logic.startsWith('range_grid_')) return '목표가·손절가·모멘텀·품질 기준을 조합한 자동 검증 grid 전략입니다.';
  return '과거 검증 결과로 생애주기를 관리하는 전략 DNA입니다.';
}



function strategyQualityLabel(row) {
  const samples = Number(row.samples || 0);
  const excess = Number(row.avg_excess_return_pct || 0);
  const recent = Number(row.recent_avg_excess_return_pct || 0);
  const success = Number(row.success_rate_pct || 0);
  if (row.status === 'active') return '추천 사용 후보';
  if (samples < 20) return '샘플 부족';
  if (excess > 0 && recent >= -1 && success >= 45) return '관찰 유지';
  if (excess < 0 || recent < -3) return '보수적 취급';
  return '검증대기';
}

function renderStrategyEngineCard(row) {
  const id = `strategy-detail-${String(row.logic || '').replace(/[^a-zA-Z0-9_-]/g,'_')}`;
  const strengths = row.summary?.strengths || [];
  const weaknesses = row.summary?.weaknesses || [];
  const strengthText = strengths.slice(0,2).map((x) => `${nameOf(x.symbol)} ${pct(x.avg_excess_return_pct)}`).join(' · ') || '-';
  const weaknessText = weaknesses.slice(0,2).map((x) => `${nameOf(x.symbol)} ${pct(x.avg_excess_return_pct)}`).join(' · ') || '-';
  const narrative = row.summary?.narrative || row.reason || strategyDescription(row) || '-';
  return `<article class="strategy-engine-card readable-card ${row.status || 'unknown'}">
    <div class="strategy-engine-head">
      <div><strong>${strategyKoreanLabel(row.logic)}</strong><code>${row.logic || '-'}</code><p>${strategyQualityLabel(row)} · 종목추천에 쓰일 수 있는 전략 DNA를 paper-only로 평가합니다.</p></div>
      <span class="${badgeClass(row.status)}">${statusLabel(row.status)}</span>
    </div>
    <div class="strategy-engine-core">
      <div class="strategy-engine-score"><b>${pct(row.avg_excess_return_pct)}</b><span>평균 초과수익</span><em>최근 ${pct(row.recent_avg_excess_return_pct)}</em></div>
      <div><span>성공률</span><b>${pct(row.success_rate_pct)}</b><em>${row.samples ?? '-'} samples</em></div>
      <div><span>최근 성과</span><b>${pct(row.recent_avg_excess_return_pct)}</b><em>${row.recent_samples ?? row.samples ?? '-'} recent</em></div>
      <div><span>검증상태</span><b>${strategyQualityLabel(row)}</b><em>${row.status || '-'}</em></div>
    </div>
    <div class="strategy-engine-reason"><b>판정</b><span>${narrative}</span></div>
    <div class="strategy-engine-evidence-row">
      <span><b>강점 종목</b>${strengthText}</span>
      <span><b>약점 종목</b>${weaknessText}</span>
    </div>
    <button class="rec-detail-toggle strategy-engine-toggle" onclick="toggleRecDetail('${id}')">전략 evidence / 샘플 상세 보기</button>
    <div id="${id}" class="strategy-engine-detail rec-lazy-detail" hidden>
      <section><h4>성과 근거</h4><p>성공률 ${pct(row.success_rate_pct)} · 평균 초과수익 ${pct(row.avg_excess_return_pct)} · 최근 초과수익 ${pct(row.recent_avg_excess_return_pct)} · 샘플 ${row.samples ?? '-'}</p></section>
      <section><h4>강점/약점</h4><p><b>강점</b> ${strengthText}</p><p><b>약점</b> ${weaknessText}</p></section>
      <section><h4>추천엔진 내 역할</h4><p>전략 DNA는 종목추천의 내부 판단 재료입니다. Fund/시장/전략 신뢰 evidence와 섞지 않고 추천 카드에서 별도 근거로 연결됩니다.</p></section>
    </div>
  </article>`;
}

function auditOutcomeText(row) {
  const result = row.result === 'success' ? '목표가 도달' : row.result === 'fail' ? '손절 기준 먼저 도달' : '기간 내 미확정';
  const rel = (row.excess_return_pct ?? 0) >= 0 ? '벤치마크 대비로는 방어/초과했습니다' : '벤치마크 대비 열위였습니다';
  const path = row.result === 'success'
    ? `진입 후 ${row.days_to_event ?? '-'}일 만에 목표가 ${fmt(row.target)}에 도달했습니다.`
    : row.result === 'fail'
      ? `진입 후 ${row.days_to_event ?? '-'}일 만에 손절 기준 ${fmt(row.stop)}을 먼저 터치했습니다.`
      : `${row.horizon_days ?? '-'}거래일 안에 목표/손절 어느 쪽도 확정되지 않았습니다.`;
  const range = `최대상승 ${pct(row.max_upside_pct)}, 최대하락 ${pct(row.max_drawdown_pct)}.`;
  return `${result}: ${path} ${rel}. ${range}`;
}

function renderUniverse(items) {
  const shouldRenderUniverse = shouldRenderTarget('symbol-universe-list') || shouldRenderTarget('universe-cards');
  if (!shouldRenderUniverse) return;
  let rows = [...(items || [])].map((row) => ({ ...row, payload: row.payload || {} }));
  const universeMarketSelect = document.getElementById('universe-market-filter');
  if (universeMarketSelect) universeMarketSelect.value = universeMarketFilter;
  if (universeMarketFilter !== 'all') rows = rows.filter((row) => marketOf(row.symbol) === universeMarketFilter);
  if (universeStatusFilter !== 'all') rows = rows.filter((row) => row.status === universeStatusFilter);
  if (universeSearchText) {
    const q = universeSearchText.toLowerCase();
    rows = rows.filter((row) => `${row.symbol} ${nameOf(row.symbol)} ${row.reason || ''}`.toLowerCase().includes(q));
  }
  const sorters = {
    score: (a, b) => (b.score || 0) - (a.score || 0),
    name: (a, b) => (nameOf(a.symbol)).localeCompare(nameOf(b.symbol)),
    bars: (a, b) => ((b.payload?.checks?.price_bars || 0) - (a.payload?.checks?.price_bars || 0)),
    updated: (a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0),
  };
  rows.sort(sorters[universeSortKey] || sorters.score);
  const rankMap = universeMarketFilter === 'all' ? recommendationSymbolRanks : (recommendationMarketRanks.get(universeMarketFilter) || new Map());
  rows.sort((a, b) => {
    const ar = rankMap.has(a.symbol) ? rankMap.get(a.symbol) : 9999;
    const br = rankMap.has(b.symbol) ? rankMap.get(b.symbol) : 9999;
    return ar - br;
  });
  const all = items || [];
  const kr = all.filter((row) => marketOf(row.symbol) === 'KR').length;
  const us = all.filter((row) => marketOf(row.symbol) === 'US').length;
  const active = all.filter((row) => row.status === 'active').length;
  const recVisible = rows.filter((row) => rankMap.has(row.symbol)).length;
  setHtml('universe-summary', `추천 ${recVisible}개 우선 · 표시 ${rows.length}/${all.length} · KR ${kr} · US ${us} · 활성 ${active}`);
  const cardRenderer = (row) => {
    const checks = row.payload?.checks || {};
    const recRank = rankMap.get(row.symbol);
    return `<article class="universe-card ${row.status} symbol-universe-card ${selectedUniverseSymbol === row.symbol ? 'selected' : ''}" data-symbol="${row.symbol}">
      <div class="audit-card-top"><strong>${nameOf(row.symbol)}</strong><span class="${badgeClass(row.status)}">${statusLabel(row.status)}</span></div>
      ${recRank !== undefined ? `<div class="history-card-meta"><span class="badge success">${universeMarketFilter === 'all' ? '추천현황' : (universeMarketFilter === 'KR' ? '한국 추천' : '미국 추천')} #${recRank + 1}</span></div>` : ''}
      <div class="audit-sub">${marketOf(row.symbol) === 'KR' ? '한국' : '미국'} · 점수 ${fmt(row.score)} · ${row.updated_at ? new Date(row.updated_at).toLocaleDateString() : '-'}</div>
      <div class="strategy-metrics">
        <div><span>가격 데이터</span><b>${checks.price_bars ?? '-'}</b></div>
        <div><span>공시</span><b>${checks.disclosures ?? 0}</b></div>
        <div><span>고위험</span><b>${checks.high_risk ?? 0}</b></div>
        <div><span>중위험</span><b>${checks.medium_risk ?? 0}</b></div>
      </div>
      <div class="audit-foot">${row.reason || row.payload?.reason || '-'}</div>
    </article>`;
  };
  if (document.getElementById('symbol-universe-list')) {
    renderCards('symbol-universe-list', rows, cardRenderer, 30);
  }
  if (document.getElementById('universe-cards')) {
    renderCards('universe-cards', rows, cardRenderer, 30);
  }
  if (activeMonitorTab() === 'symbols' && !selectedUniverseSymbol && rows[0]?.symbol) {
    selectedUniverseSymbol = rows[0].symbol;
    renderSymbolOverview(rows[0].symbol).catch(console.error);
  }
}

function renderStrategies(items) {
  let rows = [...(items || [])];
  if (strategyFilterStatus !== 'all') rows = rows.filter((row) => row.status === strategyFilterStatus);
  if (strategySearchText) {
    const q = strategySearchText.toLowerCase();
    rows = rows.filter((row) => `${row.logic} ${strategyKoreanLabel(row.logic)} ${statusLabel(row.status)}`.toLowerCase().includes(q));
  }
  const statusRank = { active: 5, watch: 4, probation: 3, candidate: 2, pending_validation: 2, retired: 1 };
  const sorters = {
    status: (a, b) => (statusRank[b.status] || 0) - (statusRank[a.status] || 0) || (b.avg_excess_return_pct || 0) - (a.avg_excess_return_pct || 0),
    excess: (a, b) => (b.avg_excess_return_pct || 0) - (a.avg_excess_return_pct || 0),
    success: (a, b) => (b.success_rate_pct || 0) - (a.success_rate_pct || 0),
    samples: (a, b) => (b.samples || 0) - (a.samples || 0),
    recent: (a, b) => (b.recent_avg_excess_return_pct || 0) - (a.recent_avg_excess_return_pct || 0),
  };
  rows.sort(sorters[strategySortKey] || sorters.status);
  const counts = rows.reduce((acc, row) => { const key = row.status || 'unknown'; acc[key] = (acc[key] || 0) + 1; return acc; }, {});
  setHtml('strategy-summary', `표시 ${rows.length}개 / 전체 ${(items || []).length}개 · active ${counts.active || 0} · watch ${counts.watch || 0} · candidate ${counts.candidate || counts.pending_validation || 0} · retired ${counts.retired || 0}`);
  renderCards('strategy-cards', rows, (row) => renderStrategyEngineCard(row), 24);
}

const AGENT_STRATEGY_CATALOG = {
  pipeline_smoke_check: {
    name: 'Pipeline Smoke Check', role: '조직 부팅 전 안전 점검',
    strategy: 'scheduled pipeline에 포함된 에이전트들의 Python compile, main guard, packet read 위험을 먼저 확인해 런타임 장애를 조기에 잡는다.',
    guardrail: '실제 추천/검증을 만들지 않고, 조직 루프가 안전하게 실행될 수 있는지 확인하는 사전 점검 역할이다.'
  },
  universe_discovery: {
    name: 'Universe Discovery', role: '신규 종목 발굴/가격 수집',
    strategy: 'US/KR 후보 universe를 넓히고 가격 데이터를 수집해 연구 가능한 종목 풀을 확장한다.',
    guardrail: '발굴은 연구 후보 추가일 뿐이며 추천/매수 신호가 아니다. 데이터 품질과 검증 루프를 통과해야 한다.'
  },
  daily_price_refresh: {
    name: 'Daily Price Refresh', role: 'active/watch universe 일봉 현행화',
    strategy: 'active/watch/open universe와 벤치마크의 최근 일봉을 yfinance adjusted data로 incremental refresh한다.',
    guardrail: '실시간/분봉 매매용 데이터가 아니라 historical/paper research용 일봉 freshness를 관리한다.'
  },
  opendart_disclosures: {
    name: 'OpenDART Disclosures', role: '한국 공시 리스크 수집',
    strategy: 'active KR 종목의 OpenDART 공시를 가져와 high/medium/positive risk context를 추천과 universe 관리에 공급한다.',
    guardrail: '공시는 look-ahead bias를 피해야 하며 positive 공시는 자동 승격이 아니라 보조 근거로만 쓴다.'
  },
  sec_edgar_disclosures: {
    name: 'SEC EDGAR Disclosures', role: '미국 공시 리스크 수집',
    strategy: 'active US 종목의 SEC filing 이벤트를 수집해 추천 리스크와 위원회 판단에 반영한다.',
    guardrail: 'filing 제목/유형 기반 분류는 보수적으로 해석하고, 가격/검증 신호를 대체하지 않는다.'
  },
  universe_curator: {
    name: 'Universe Curator', role: '연구 universe 위생 관리',
    strategy: '발굴 후보를 active/watch/quarantine/retired로 정리해 검증 자원이 너무 넓게 퍼지지 않도록 한다.',
    guardrail: '데이터 부족, 과도한 이벤트 리스크, 낮은 유동성은 연구 대상에서 밀어낸다.'
  },
  opendart_financials: {
    name: 'OpenDART Financials', role: 'KR 재무 스냅샷 보강',
    strategy: '한국 active universe에 대해 OpenDART 재무제표 스냅샷을 수집해 가격 기반 신호의 재무 맥락을 보강한다.',
    guardrail: '재무 데이터는 보조 맥락이며, 결측/지연 가능성이 있어 단독 추천 근거로 쓰지 않는다.'
  },
  data_quality: {
    name: 'Data Quality Agent', role: '가격 데이터 품질 점검',
    strategy: '결측, 이상 가격, 부족한 bar, stale 데이터 등 검증을 왜곡할 수 있는 입력 품질 문제를 탐지한다.',
    guardrail: '데이터 품질 문제가 있으면 추천보다 원천 데이터 복구/제외 판단을 우선한다.'
  },
  strategy_generator: {
    name: 'Strategy Generator', role: '전략 후보 생성',
    strategy: 'range/grid/모멘텀 계열 전략 후보를 만들고 registry에 등록해 검증 backlog를 확장한다.',
    guardrail: '생성은 후보 등록일 뿐이며 active가 되려면 충분한 샘플과 초과수익 조건을 만족해야 한다.'
  },
  capacity_planner: {
    name: 'Validation Capacity Planner', role: '검증 처리량 조절',
    strategy: 'coverage, pending backlog, 서버 여유를 보고 validation batch-size를 자동 권고/적용한다.',
    guardrail: '속도보다 안정성을 우선하며, 서버 부하가 커지면 검증량을 줄이는 쪽으로 동작해야 한다.'
  },
  simulation_validation_worker: {
    name: 'Simulation Validation Worker', role: '과거 시점 검증 실행',
    strategy: '종목×cutoff×전략×horizon backlog를 소비하며 미래 데이터를 보지 않는 historical validation을 누적한다.',
    guardrail: '실거래 주문 없이 historical/paper 검증만 수행하고, 샘플 편중을 줄이기 위해 under-tested 전략을 우선한다.'
  },
  strategy_novelty_pruner: {
    name: 'Strategy Novelty Pruner', role: '중복/과최적화 후보 축소',
    strategy: '유사한 grid 전략과 낮은 차별성 후보를 묶어 hold/merge 적용으로 후보 폭발과 과최적화를 줄인다.',
    guardrail: '성과 좋은 전략을 삭제하지 않고, 중복 후보의 검증 우선순위를 낮추는 보수적 pruning을 한다.'
  },
  strategy_lifecycle: {
    name: 'Strategy Lifecycle Agent', role: '전략 승격/감시/퇴역',
    strategy: '성공률, 평균 초과수익, 최근 성과, 샘플 수를 기준으로 active/watch/probation/retired 상태를 관리한다.',
    guardrail: 'retired는 코드 삭제가 아니라 비활성화이며, 새 데이터에서 회복하면 재검증/부활 가능하다.'
  },
  active_strategy_balancer: {
    name: 'Active Strategy Balancer', role: 'active pool 균형 유지',
    strategy: 'active 전략 수가 부족할 때 near-qualified 후보를 제한적으로 승격해 추천 엔진이 너무 빈약해지지 않게 한다.',
    guardrail: '강제 승격이 아니라 최소 샘플/성과 문턱을 통과한 후보만 보수적으로 보강한다.'
  },
  strategy_success_optimizer: {
    name: 'Strategy Success Optimizer', role: '전략 성공률 개선 게이트',
    strategy: '전략별 성공률, 초과수익, 최근 성과, 종목별 강약점을 평가해 추천 사용 가능/연구 유지/비활성 후보를 나눈다.',
    guardrail: '승률을 높이기 위한 품질 게이트이며, 실패 전략을 낙관적으로 보정하지 않고 추천 사용 범위를 줄인다.'
  },
  recommendation_agent: {
    name: 'Recommendation Agent', role: '현재 추천 후보 생성',
    strategy: 'active 검증 전략을 현재 가격/벤치마크/시장 구분에 적용해 paper 추천 후보와 목표/손절가를 만든다.',
    guardrail: '실거래 주문이 아니며, 공시·regime·critic·portfolio 필터를 거쳐야 최종 검토 가치가 있다.'
  },
  recommendation_critic: {
    name: 'Recommendation Critic', role: '추천 반대 근거 점검',
    strategy: '추천 후보별 약점, 과신 가능성, 데이터 부족, 최근 성과 둔화 등 반대 논리를 붙인다.',
    guardrail: '추천을 무조건 막는 역할이 아니라 사용자가 위험을 같이 보도록 균형을 맞춘다.'
  },
  portfolio_risk_manager: {
    name: 'Portfolio Risk Manager', role: '포트폴리오/집중 리스크 점검',
    strategy: '시장/섹터/종목 쏠림, 상관 위험, 후보 간 중복 exposure를 확인해 추천 리스트에 리스크 메모를 붙인다.',
    guardrail: 'paper research 맥락의 위험 주석이며 실제 포지션/주문 관리가 아니다.'
  },
  market_regime_gate: {
    name: 'Market Regime Gate', role: '시장 환경 필터',
    strategy: '벤치마크 추세/변동성/regime을 보고 현재 추천 신호가 불리한 환경에서 나온 것인지 점검한다.',
    guardrail: 'regime은 참고 필터이며, 데이터가 부족하면 보수적으로 “검토 필요”로 남긴다.'
  },
  recommendation_audit: {
    name: 'Strategy Trust Audit', role: '전략 신뢰도·조건 감사',
    strategy: 'active 전략 추천 로직을 과거 cutoff에 재적용해 성공/실패/timeout과 초과수익을 샘플링한다.',
    guardrail: '미래 데이터 누수 없이 cutoff 기준으로만 평가하고, audit preview는 판단 보조 자료로 사용한다.'
  },
  outcome_attribution: {
    name: 'Outcome Attribution', role: '검증 결과 원인 추정',
    strategy: '성공/실패 audit 결과에 시장, 종목, 전략, 변동성, 이벤트 맥락을 붙여 왜 그런 결과가 나왔는지 설명한다.',
    guardrail: '원인 추정은 확정 진단이 아니라 패턴 설명이며, 다음 검증/개선 후보를 찾는 데 쓴다.'
  },
  committee_performance_ledger: {
    name: 'Committee Performance Ledger', role: '위원회 판단 이력/성과 기록',
    strategy: '투자위원회 support/watch/reject 판단과 이후 outcome을 연결해 위원회 보수성/공격성이 실제 성과와 맞는지 추적한다.',
    guardrail: '판단 이력 기록용이며 단독으로 추천을 바꾸지 않는다.'
  },
  recommendation_outcome_tracker: {
    name: 'Recommendation Outcome Tracker', role: '추천 사후성과 추적',
    strategy: 'recommendation_history의 1D/5D/20D forward return, 벤치마크 대비 초과수익, hit 여부를 recommendation_outcomes에 누적한다.',
    guardrail: '아직 기간이 지나지 않은 추천은 pending으로 두고 성과 통계에 섞지 않는다.'
  },
  recommendation_funnel: {
    name: 'Recommendation Funnel Agent', role: '추천 후보 선별 흐름 계측',
    strategy: '발굴, 데이터, scout, 추천, critic, committee 단계별 통과/탈락 규모를 기록해 병목과 과도한 차단 지점을 드러낸다.',
    guardrail: '추천을 직접 바꾸지 않고 관측 지표만 만든다.'
  },
  recommendation_calibration: {
    name: 'Recommendation Calibration', role: '판단 품질 보정',
    strategy: '추천 점수, critic severity, committee decision, 재무 bucket별 forward outcome을 비교해 점수와 차단 기준이 성과와 맞는지 점검한다.',
    guardrail: '완료 outcome 표본이 적으면 threshold 변경 대신 관찰 경고로 남긴다.'
  },
  supply_weight_evaluator: {
    name: 'Supply Weight Evaluator', role: '수급/거래주체 가중치 검증',
    strategy: '수급/종가강도 및 외국인·기관 seed 보정값을 recommendation outcome과 구간별로 비교해 keep/upweight/downweight 제안을 만든다.',
    guardrail: 'proposal-only이며 표본 부족 시 가중치 변경 없이 hold_collect_samples로 둔다.'
  },
  investment_committee: {
    name: 'Investment Committee', role: '공격/안전/중립 의견 종합',
    strategy: '공격투자형, 안전투자형, 중립형 평가를 각각 만든 뒤 가중 종합해 추천을 support/watch/reject로 재판정한다.',
    guardrail: '투자성향별 해석은 paper research 평가 레이어이며 실제 주문/포지션 지시가 아니다.'
  },
  current_recommendation_validation: {
    name: 'Current Recommendation Validation', role: '현재 추천 후보 우선 검증',
    strategy: '현재 화면에 오른 추천/관찰 후보의 종목과 전략을 우선 검증해 종목별 샘플 부족을 빠르게 줄인다.',
    guardrail: '전체 백로그를 대체하지 않고, 현재 의사결정 품질을 높이기 위한 우선순위 큐로만 동작한다.'
  },
  org_improvement_guardian: {
    name: 'Org Improvement Guardian', role: '개선안 자동적용 가드레일',
    strategy: '조직평가 findings를 자동적용/관찰/승인필요로 분류하고, low-risk reversible maintenance만 자동 적용한다.',
    guardrail: '전략 임계값, evaluator 추가/제거, pipeline 구조 변경, 외부 서비스 변경은 자동 적용하지 않고 승인 대상으로 남긴다.'
  },
  strategy_director: {
    name: 'Strategy Director', role: '전략 총괄',
    strategy: '전략 생성/검증/수명주기/audit agent들이 자기 역할대로 일하는지 평가하고 다음 cycle 검증 과제를 배정한다.',
    guardrail: '전략 상태의 canonical writer는 strategy_lifecycle이며, 총괄은 평가/배정 contract만 낸다.'
  },
  fund_director: {
    name: 'Fund Director', role: 'Fund 총괄',
    strategy: 'paper fund 리그, registry, 성과평가, risk guardian, consensus 품질을 관리하고 fund risk가 과잉 차단 신호가 되지 않게 감시한다.',
    guardrail: 'fund consensus는 추천 overlay이며 실제 주문/단독 매수 신호가 아니다.'
  },
  recommendation_desk_lead: {
    name: 'Recommendation Desk Lead', role: '추천 총괄',
    strategy: '추천 생성, critic, risk, regime, committee, validation이 서로 다른 의견 타입을 내는지 평가한다.',
    guardrail: '최종 추천 필드는 기존 recommendation/committee flow가 유지하고 총괄은 역할 적합성과 병목만 판정한다.'
  },
  executive_director: {
    name: 'Executive Organization Director', role: '전체 조직 총괄',
    strategy: '도메인 총괄과 suborg compact 상태를 읽어 조직 병목, escalation, 다음 cycle 우선순위를 정리한다.',
    guardrail: '조직 감독 contract만 발행하며 추천 승인, 정책 변경, 주문을 직접 수행하지 않는다.'
  },
  org_evaluator: {
    name: 'Org Evaluator', role: '조직 메타 평가',
    strategy: 'coverage, active pool, 실패/스킵, 중복/과최적화, 신규 에이전트 필요성을 평가하고 개선 제안을 만든다.',
    guardrail: '조직 운영 개선을 위한 평가이며 추천/주문 판단과 분리한다.'
  },
};

function normalizeAgentKey(name) {
  return String(name || '').replace(/\.py$/,'').replace(/-/g,'_').toLowerCase();
}

function humanizeAgentName(name) {
  return String(name || '')
    .replace(/\.py$/,'')
    .replace(/[_-]+/g,' ')
    .replace(/\s+/g,' ')
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase()) || 'Unknown Agent';
}

function agentStrategyDescriptions(pipelineSteps = []) {
  const seen = new Set();
  const rows = [];
  (pipelineSteps || []).forEach((step) => {
    const key = normalizeAgentKey(step.agent || (step.cmd || [])[1]?.split('/').pop());
    if (!seen.has(key)) {
      seen.add(key);
      const meta = AGENT_STRATEGY_CATALOG[key] || {};
      rows.push({
        key,
        name: step.display_name || meta.name || key,
        role: step.role_summary || meta.role || '역할 요약 미등록',
        strategy: step.description || step.strategy || meta.strategy || (step.role_summary ? `${step.role_summary} 역할을 수행하며 pipeline artifact와 contract를 남긴다.` : 'pipeline 실행 결과와 output contract로 역할 수행 여부를 확인한다.'),
        guardrail: meta.guardrail || '권한이 명시되지 않은 agent는 proposal/context/validation 출력만 생성하며 추천 승인이나 주문을 직접 수행하지 않는다.',
        status: step.status || '-',
        last_run: step.ended_at || step.started_at,
        warnings: step.warnings || []
      });
    }
  });
  return rows;
}


function supervisorDomainLabel(key, row = {}) {
  const labels = {
    executive_director: 'Executive Director',
    data_steward: 'Data / Price Office',
    market_context_director: 'Market Context',
    strategy_director: 'Strategy Lab',
    fund_director: 'Fund Research',
    recommendation_desk_lead: 'Recommendation Desk',
    governance_director: 'Governance',
    org_evaluator: 'Org Evaluator'
  };
  return row.title || labels[key] || key;
}

function supervisorStatusClass(status) {
  const value = String(status || '').toLowerCase();
  if (['ok','healthy','normal'].includes(value)) return 'good';
  if (['watch','degraded','action_required'].includes(value)) return 'warn';
  if (['urgent','failed','failed_required','error'].includes(value)) return 'bad';
  return 'neutral';
}

function supervisorStatusLabel(status) {
  const value = String(status || '').toLowerCase();
  const labels = {
    ok: '정상', healthy: '정상', normal: '정상',
    watch: '관찰', degraded: '주의', action_required: '조치 필요',
    urgent: '긴급', failed: '실패', failed_required: '복구 필요', error: '오류',
    unknown: '확인 중'
  };
  return labels[value] || humanizeAgentName(value || 'unknown');
}

function readableSupervisorSummary(row = {}) {
  const raw = row.display_summary || row.role_summary || row.bottleneck || row.architecture_summary || row.purpose || row.domain || row.summary_text || '';
  const text = String(raw || '도메인 supervisor 상태와 관리 agent 묶음입니다.');
  const replacements = {
    data_freshness_or_quality_requires_repair: '가격/데이터 신선도 또는 품질 보강이 필요합니다.',
    no_high_confidence_historical_active_strategy: '고신뢰 historical active 전략 풀이 부족해 후보 확장이 필요합니다.',
    no_action_required: '즉시 조치가 필요한 항목은 없습니다.'
  };
  return replacements[text] || text.replace(/_/g, ' ');
}

function readableSupervisorMetricText(row = {}) {
  const metrics = row.metrics || row.summary || {};
  const pairs = [
    ['review_count', '검토'],
    ['action_count', '조치'],
    ['watch_count', '관찰'],
    ['scheduled_agent_count', '스케줄'],
    ['role_fitness_avg', '적합도'],
    ['guardian_auto_applied', '자동처리'],
    ['health_score', '건강점수'],
  ];
  const parts = pairs
    .map(([key, label]) => metrics?.[key] != null ? `${label}: ${fmt(metrics[key])}` : null)
    .filter(Boolean);
  if (row.role_fitness_avg != null && !parts.some((part) => part.startsWith('적합도'))) parts.push(`적합도: ${fmt(row.role_fitness_avg)}`);
  return parts.slice(0, 3).join(' · ');
}

function supervisorOwnedAgents(row = {}) {
  const owned = row.owned_agents || row.managed_agents || row.agents || [];
  return (owned || []).map((agent) => typeof agent === 'string' ? agent : (agent.agent || agent.name || agent.key || agent.owner_agent || agent.director || agent.suborg)).filter(Boolean);
}


function supervisorManagedItems(row = {}) {
  const owned = firstNonEmptyList(row.managed_agent_details, row.owned_agent_details, row.owned_agents, row.managed_agents, row.agents);
  return (owned || []).map((item) => {
    if (typeof item === 'string') return { key: item };
    const key = item.agent_name || item.agent || item.name || item.key || item.owner_agent || item.director || item.suborg;
    return key ? { ...item, key } : null;
  }).filter(Boolean);
}

function firstNonEmptyList(...lists) {
  return lists.find((list) => Array.isArray(list) && list.length) || [];
}

function stripRepeatedRolePrefix(text, role) {
  const body = String(text || '').trim();
  const roleText = String(role || '').trim();
  if (!body || !roleText) return body;
  const escapedRole = roleText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return body
    .replace(new RegExp(`^${escapedRole}[\\s.。:：-]+`, 'i'), '')
    .trim();
}

function compactRoleAndStrategy(role, strategy) {
  const cleanRole = String(role || '').trim();
  const cleanStrategy = stripRepeatedRolePrefix(strategy, cleanRole);
  if (!cleanStrategy || cleanStrategy === cleanRole) return cleanRole;
  return `${cleanRole}<br>${cleanStrategy}`;
}

function agentStatusBadge(status) {
  const raw = String(status || '').trim();
  if (!raw || raw === '-') return { className: 'neutral', label: '역할 정의' };
  return { className: supervisorStatusClass(raw), label: supervisorStatusLabel(raw) };
}

function supervisorAssignments(row = {}) {
  return (row.next_cycle_assignments || row.next_actions || row.escalations || []).map((item) => {
    if (typeof item === 'string') return item;
    return item.assignment || item.next_action || item.reason || item.title || item.owner_agent || '';
  }).filter(Boolean);
}

function renderSupervisorDomainCards(pipe = {}, evalPayload = {}, guardianPayload = {}) {
  if (!document.getElementById('org-topology')) return;
  const agentRoleLookup = {};
  agentStrategyDescriptions(pipe.steps || []).forEach((agent) => {
    agentRoleLookup[normalizeAgentKey(agent.key)] = agent;
    agentRoleLookup[normalizeAgentKey(agent.name)] = agent;
  });
  const agentStrategyCard = (agent) => {
    const item = typeof agent === 'string' ? { key: agent } : (agent || {});
    const key = item.key || item.agent_name || item.agent || item.name || item.owner_agent || item.director || item.suborg;
    const meta = agentRoleLookup[normalizeAgentKey(key)] || AGENT_STRATEGY_CATALOG[normalizeAgentKey(key)] || {};
    const name = item.display_name || item.title || meta.name || humanizeAgentName(key);
    const role = item.role_summary || meta.role || item.domain || '역할 요약 미등록';
    const strategy = item.description || item.strategy || item.bottleneck || meta.strategy || `${role} 역할을 수행하며 pipeline artifact와 contract를 남긴다.`;
    const guardrail = item.guardrail || item.guardrails || meta.guardrail || '권한이 명시되지 않은 agent는 proposal/context/validation 출력만 생성하며 추천 승인이나 주문을 직접 수행하지 않는다.';
    const status = item.status || item.domain_status || meta.status || '-';
    const statusBadge = agentStatusBadge(status);
    const lastRun = meta.last_run ? new Date(meta.last_run).toLocaleString() : '';
    const warnings = (meta.warnings || []).length ? ` · warnings ${(meta.warnings || []).length}` : '';
    const detail = item.role_fitness_avg != null ? `fitness ${fmt(item.role_fitness_avg)}` : (item.assignment_count != null ? `assignments ${item.assignment_count}` : '');
    return `<article class="audit-card agent-strategy org-supervisor-agent-card">
      <div class="audit-card-top"><strong>${name}</strong><span class="badge ${statusBadge.className}">${statusBadge.label}</span></div>
      <div class="audit-sub">${compactRoleAndStrategy(role, strategy)}</div>
      <div class="audit-foot"><b>가드레일</b>: ${guardrail}${detail ? `<br><small>${detail}</small>` : ''}${lastRun ? `<br><small>latest: ${lastRun}${warnings}</small>` : ''}</div>
    </article>`;
  };
  const supervisors = pipe.domain_supervisors || pipe.research_org_suborg_summary?.domain_supervisors || {};
  const executive = pipe.executive_director || pipe.executive_director_summary || pipe.domain_supervisors?.executive_director || {};
  const managementTree = pipe.management_tree || pipe.research_org_suborg_summary?.management_tree || executive.management_tree || {};
  const executiveManagedAgents = firstNonEmptyList(
    executive.managed_directors,
    executive.manages,
    executive.managed_agents,
    managementTree.executive_director?.manages,
    executive.managed_suborgs
  );
  const rows = [];
  if (executive.supervisor || executive.org_status || executive.managed_directors || executive.managed_suborgs) {
    rows.push(['executive_director', {
      title: executive.title || 'Executive Director',
      supervisor: executive.supervisor || 'executive_director',
      domain_status: executive.org_status || executive.status || executive.contract?.status,
      owned_agents: executiveManagedAgents,
      next_cycle_assignments: executive.next_cycle_priorities || executive.escalations || [],
      summary: executive.architecture_summary || executive.summary
    }]);
  }
  Object.entries(supervisors).forEach(([key, value]) => rows.push([key, value || {}]));
  if (evalPayload.verdict || guardianPayload.summary) {
    rows.push(['org_evaluator', {
      title: 'Org Evaluator / Guardian',
      supervisor: 'org_evaluator',
      domain_status: evalPayload.verdict || guardianPayload.contract?.status || 'watch',
      owned_agents: ['org_evaluator', 'org_improvement_guardian'],
      next_cycle_assignments: (evalPayload.findings || []).slice(0, 3).map((f) => f.recommendation || f.finding).filter(Boolean),
      summary: { health_score: evalPayload.health_score, guardian_auto_applied: guardianPayload.summary?.auto_applied_count }
    }]);
  }
  const unique = [];
  const seen = new Set();
  rows.forEach(([key, row]) => {
    const id = row.supervisor || key;
    if (seen.has(id)) return;
    seen.add(id);
    unique.push([key, row]);
  });
  const cards = unique.map(([key, row]) => {
    const status = row.domain_status || row.org_status || row.status || row.contract?.status || 'unknown';
    const agents = supervisorManagedItems(row);
    const assignments = supervisorAssignments(row);
    const managedLabel = key === 'executive_director' ? '관리 supervisor' : '관리 agent';
    const metrics = row.metrics || row.summary || {};
    const fitness = row.role_fitness_avg ?? row.metrics?.role_fitness_avg;
    const metricText = readableSupervisorMetricText(row) || (fitness != null ? `적합도: ${fmt(fitness)}` : '');
    return `<article class="org-supervisor-card ${supervisorStatusClass(status)}">
      <div class="org-supervisor-head"><div><strong>${supervisorDomainLabel(key, row)}</strong><span>${row.supervisor || key}</span></div><em>${supervisorStatusLabel(status)}</em></div>
      <p>${readableSupervisorSummary(row)}</p>
      <section class="org-supervisor-agents">
        <div class="org-supervisor-agent-heading"><b>${managedLabel}</b><small>${agents.length || 0}개</small></div>
        <div class="org-supervisor-agent-list">${agents.map(agentStrategyCard).join('') || '<span>-</span>'}</div>
      </section>
      <footer>${metricText || 'contract 대기'}${assignments.length ? `<br>${assignments.slice(0, 2).join('<br>')}` : ''}</footer>
    </article>`;
  }).join('');
  setHtml('org-topology', `<div class="org-supervisor-grid">${cards || '<div class="empty-state">Supervisor/domain 상태를 불러오는 중입니다.</div>'}</div>`);
}

function fundStyleDescription(style){
  const m={trend:'중기 추세/이동평균 정렬을 선호',breakout:'고점 돌파와 거래량 확장을 선호',balanced:'추세·변동성·현금비중을 균형 있게 반영',defensive:'현금비중과 낮은 변동성을 우선',mean_reversion:'상승 추세 내 눌림목 회복을 선호',volume_surge:'거래량 급증과 단기 모멘텀을 선호'};
  return m[style] || '혼합형 fund';
}
function targetFromConsensus(row){
  const price=Number(row.last_price || row.fund_details?.[0]?.buy_price || 0);
  if(!price) return {target:null,stop:null};
  const styles=row.fund_styles||{};
  const aggressive=(styles.breakout||0)+(styles.volume_surge||0);
  const targetPct=aggressive>=3?8:6;
  const stopPct=aggressive>=3?-6:-5;
  return {target:price*(1+targetPct/100),stop:price*(1+stopPct/100),targetPct,stopPct};
}


function pricePlanFromConsensus(row){
  const base=Number(row.analysis_price || row.last_price || row.fund_details?.[0]?.buy_price || 0);
  if(!base) return {base:null,conservative:null,center:null,upper:null,stop:null,centerPct:null,stopPct:null};
  const styles=row.fund_styles||{};
  const aggressive=(styles.breakout||0)+(styles.volume_surge||0);
  const centerPct=Number(row.target_return_pct || row.expected_return_pct || (aggressive>=3?8:6));
  const stopPct=Number(row.stop_loss_pct || (aggressive>=3?-6:-5));
  const conservativePct=Math.max(2, centerPct*0.6);
  const upperPct=centerPct + Math.max(2, centerPct*0.45);
  return {base,conservative:base*(1+conservativePct/100),center:base*(1+centerPct/100),upper:base*(1+upperPct/100),stop:base*(1+stopPct/100),conservativePct,centerPct,upperPct,stopPct,date:row.analysis_price_date||row.latest_buy_date||row.asof_date};
}
function renderPricePlan(row){
  const p=pricePlanFromConsensus(row);
  return `<div class="price-plan-grid"><div><span>전일 종가</span><b>${fmt(p.base)}</b><em>${p.date||'-'}</em></div><div><span>보수 목표가</span><b>${fmt(p.conservative)}</b><em>${pct(p.conservativePct)}</em></div><div class="primary"><span>중심 목표가</span><b>${fmt(p.center)}</b><em>${pct(p.centerPct)}</em></div><div><span>상단 목표가</span><b>${fmt(p.upper)}</b><em>${pct(p.upperPct)}</em></div><div class="risk"><span>손절가</span><b>${fmt(p.stop)}</b><em>${pct(p.stopPct)}</em></div></div>`;
}

function fundStyleText(obj){ return Object.entries(obj||{}).map(([k,v])=>`${k} ${v}`).join(' · ') || '-'; }

function marketContextBlock(ctx){
  if(!ctx) return `<div class="evidence-box muted"><b>Market Evidence</b><span>지수 대비/뉴스 맥락 자료 부족</span></div>`;
  return `<div class="evidence-box market-evidence"><b>Market Evidence</b><div class="evidence-grid"><span>지수대비 5D <strong>${ctx.excess_5d_pct??'-'}%p</strong><em>${ctx.relative_label_5d||'-'}</em></span><span>지수대비 20D <strong>${ctx.excess_20d_pct??'-'}%p</strong><em>${ctx.relative_label_20d||'-'}</em></span><span>거래량 <strong>${ctx.volume_ratio_20d??'-'}x</strong><em>${ctx.volume_label||'-'}</em></span><span>기준지수 <strong>${ctx.benchmark||'-'}</strong><em>${ctx.latest_date||'-'}</em></span></div><p>${ctx.news_summary||'최근 공시/뉴스 근거 부족'}</p></div>`;
}


function showRecFundDetail(encoded, panelId){
  let d={};
  try{ d=JSON.parse(decodeURIComponent(encoded)); }catch(e){ console.warn('fund detail parse failed', e); return; }
  const panel=document.getElementById(panelId); if(!panel) return;
  const html=`<div class="rec-inline-fund-card">
    <div class="holding-detail-head"><div><strong>${d.fund_id||'-'}</strong><code>${d.style||'-'}</code></div><button class="button secondary" onclick="document.getElementById('${panelId}').innerHTML=''">닫기</button></div>
    <p>${fundStyleDescription(d.style)}. 이 fund는 ${d.buy_date||'-'}에 ${d.symbol||'-'}를 ${fmt(d.buy_price)} 기준으로 편입/추가매수했습니다.</p>
    <div class="rec-v2-stats compact">
      ${compactStat('매수근거', d.reason||'-', `score ${fmt(d.score)}`)}
      ${compactStat('Fund 품질', fmt(d.quality), d.style||'-')}
      ${compactStat('매수가', fmt(d.buy_price), d.buy_date||'-')}
      ${compactStat('역할', (String(d.reason||'').includes('pyramid')?'불타기/추가매수':'신규/일반매수'), 'paper-only')}
    </div>
  </div>`;
  panel.innerHTML=html;
}
function renderRecFundDetailRows(details,row){
  return `<div class="fund-detail-list inline-expandable">${details.map((d,i)=>{ const panelId=`rec-fund-detail-${row.symbol}-${i}`.replace(/[^a-zA-Z0-9_-]/g,'_'); const payload=encodeURIComponent(JSON.stringify(Object.assign({symbol:row.symbol},d))); return `<div class="fund-detail-row-wrap"><button class="fund-detail-row fund-detail-button" onclick="showRecFundDetail('${payload}','${panelId}')"><div><b>${d.fund_id}</b><em>${d.style||'-'} · ${fundStyleDescription(d.style)}</em></div><div><span>score ${fmt(d.score)}</span><span>매수가 ${fmt(d.buy_price)}</span><span>${d.reason||'-'}</span></div></button><div id="${panelId}" class="rec-fund-inline-panel"></div></div>`; }).join('') || '<div>상세 fund 근거 없음</div>'}</div>`;
}

function renderFundConsensusRecommendationCard(row, idx){
  const rec = row.action_label || row.recommendation_bucket || '검토대기';
  const tgt=targetFromConsensus(row);
  const details=(row.fund_details||[]).slice(0,6);
  const reasonText=Object.entries(row.buy_reasons||{}).map(([k,v])=>`${k} ${v}`).join(' · ') || '-';
  const styles=fundStyleText(row.fund_styles);
  return `<article class="recommendation-card rec-card-v2 fund-consensus-rec readable-card">
    <div class="rec-rank">#${idx+1}</div>
    <div class="rec-v2-main">
      <div class="readable-card-head">
        <div><strong>${row.name || nameOf(row.symbol)}</strong><code>${row.symbol}</code><p>${row.asof_date || '-'} 기준 상위 fund 공통 매수 · paper-only</p></div>
        <span class="badge neutral">${rec}</span>
      </div>
      <div class="readable-summary-grid">
        <div><span>공통매수</span><b>${row.buy_fund_count||0} funds</b><em>보유 ${row.holding_fund_count||0}</em></div>
        <div><span>Fund score</span><b>${fmt(row.weighted_score)}</b><em>${styles}</em></div>
        <div><span>가격계획</span><b>5단계</b><em>전일 종가 기준</em></div>
        <div><span>Risk</span><b>${rec}</b><em>${row.trade_eligible ? 'paper 후보' : '검증/보류'}</em></div>
      </div>
      <div class="readable-reason"><b>추천 이유</b><span>${reasonText}</span></div>
      ${renderPricePlan(row)}
      ${marketContextBlock(row.market_context)}
      <details class="rec-v2-details readable-details"><summary>Fund별 상세 근거 ${details.length}건 보기</summary>
        ${renderRecFundDetailRows(details,row)}
      </details>
      <div class="audit-foot"><b>참여 fund</b>: ${(row.participating_funds||[]).slice(0,8).join(', ') || '-'}<br><span class="hint">실제 주문 권한 없음 · risk/committee gate는 별도 확인</span></div>
    </div>
  </article>`;
}
async function renderPrimaryFundConsensus(recommendations={}){
  const target=document.getElementById('recommendations-cards'); if(!target) return;
  try{
    const packet=await apiWithStaticFallback('/api/research/fund/recommendation-consensus/latest','/static/fund_recommendation_consensus_latest.json',null);
    const overall=await apiWithStaticFallback('/api/research/fund/consensus/latest','/static/fund_consensus_latest.json',null);
    if(!packet || !(packet.items||[]).length) return;
    const recBySymbol=new Map((recommendations.items||[]).map(r=>[r.symbol,r]));
    const marketPacket=await apiWithStaticFallback('/api/recommendations/market-context/latest','/static/recommendation_market_context_latest.json',{items:[]});
    const marketBySymbol=new Map((marketPacket.items||[]).map(r=>[r.symbol,r]));
    const buyItems=(packet.items||[]).map(x=>Object.assign({}, recBySymbol.get(x.symbol)||{}, x, {market_context:marketBySymbol.get(x.symbol)}));
    const overallItems=(overall?.symbol_consensus||overall?.items||[]).slice(0,10);
    const section=`<section class="recommendation-section fund-evidence-section"><div class="recommendation-section-head"><div><h3>Fund consensus</h3><p>현재 추천 후보의 fund 매수 합의와 보유/가격 합의를 확인합니다.</p></div><span>${buyItems.length} 신규매수</span></div><details open><summary>최근 fund 신규매수 consensus</summary>${buyItems.map(renderFundConsensusRecommendationCard).join('')}</details>${overallItems.length?`<details><summary>Fund 전체 보유/합의 상위 ${overallItems.length}개</summary><div class="fund-overall-consensus">${overallItems.map(x=>`<span><b>${nameOf(x.symbol)||x.symbol}</b><em>${x.symbol} · votes ${x.votes??'-'} · score ${fmt(x.weighted_score)}</em></span>`).join('')}</div></details>`:''}</section>`;
    target.insertAdjacentHTML('beforeend', section);
  }catch(e){ console.warn('fund consensus render failed', e); }
}
function explainHoldingReason(h, f){
  const reasons=Array.isArray(h.reason)?h.reason:(h.reason?[String(h.reason)]:[]);
  const parts=[];
  parts.push(`Fund ${f?.id||'-'}(${f?.style||'-'})가 ${h.entry_date||'-'} 기준 ${h.symbol}을 매수한 paper position입니다.`);
  if(reasons.length) parts.push(`핵심 근거: ${reasons.join(' · ')}.`);
  if(h.score!=null) parts.push(`매수 점수 ${fmt(h.score)}점, fund 기준 ${fmt(f?.score_min)}점.`);
  parts.push(`매입가 ${fmt(h.entry_price)}, 목표가 ${fmt(h.target)}, 손절가 ${fmt(h.stop)}, 현재가 ${fmt(h.current_price)} 기준입니다.`);
  return parts.join(' ');
}
function showHoldingReason(encoded, panelId){
  let payload={};
  try{ payload=JSON.parse(decodeURIComponent(encoded)); }catch(e){ console.warn('holding payload parse failed',e); return; }
  const h=payload.h||{}; const f=payload.f||{};
  const reasons=Array.isArray(h.reason)?h.reason:(h.reason?[String(h.reason)]:[]);
  const targetPct=h.entry_price?((h.target/h.entry_price-1)*100):null;
  const stopPct=h.entry_price?((h.stop/h.entry_price-1)*100):null;
  const panel=document.getElementById(panelId||`holding-panel-${f.id}`);
  if(!panel) return;
  const html=`<div class="holding-detail-card inline">
    <div class="holding-detail-head"><div><strong>${nameOf(h.symbol)||h.symbol}</strong><code>${h.symbol||''}</code></div><button class="button secondary" onclick="document.getElementById('${panel.id}').innerHTML=''">닫기</button></div>
    <p>${explainHoldingReason(h,f)}</p>
    <div class="rec-v2-stats compact">
      ${compactStat('매입일', h.entry_date||'-', `수량 ${h.qty??'-'}`)}
      ${compactStat('매입가', fmt(h.entry_price), `현재 ${fmt(h.current_price)}`)}
      ${compactStat('목표가', fmt(h.target), pct(targetPct))}
      ${compactStat('손절가', fmt(h.stop), pct(stopPct))}
      ${compactStat('평가손익', `${fmt(h.unrealized_pnl)}원`, pct(h.unrealized_pnl_pct))}
      ${compactStat('매수점수', fmt(h.score), `기준 ${fmt(f.score_min)}`)}
    </div>
    <details open class="rec-v2-details"><summary>상세 근거</summary>
      <ul class="holding-reason-list">
        <li><b>Fund 성향</b><span>${fundStyleDescription(f.style)} · target ${pct(f.target_pct)} / stop ${pct(f.stop_pct)}</span></li>
        <li><b>매수근거</b><span>${reasons.join(' · ') || '-'}</span></li>
        <li><b>청산규칙</b><span>목표/손절은 fund DNA에서 산출. replay는 daily high/low 장중 도달 기준, 동시 도달 시 손절 우선.</span></li>
        <li><b>주의</b><span>paper-only research snapshot이며 실제 주문 권한이나 투자 권유가 아닙니다.</span></li>
      </ul>
    </details>
  </div>`;
  panel.innerHTML=html;
  panel.scrollIntoView({behavior:'smooth',block:'nearest'});
}

function fundSourceLabel(src){
  const map={live_paper:'Live paper',snapshot_replay:'Snapshot replay',price_replay:'Price replay'};
  return map[src] || src || 'fund';
}
function fundCurrentAsset(f){
  if(!f) return null;
  const v=f.current_asset ?? f.equity ?? f.asset;
  if(v!==undefined && v!==null) return Number(v);
  if(f.return_pct!==undefined && f.return_pct!==null) return 10000000 * (1 + Number(f.return_pct)/100);
  return null;
}
function fundAssetNote(f){
  if(!f) return '';
  if((f.current_asset ?? f.equity ?? f.asset)!==undefined && (f.current_asset ?? f.equity ?? f.asset)!==null) return 'reported';
  if(f.return_pct!==undefined && f.return_pct!==null) return 'return 기반 추정';
  return '자산 데이터 없음';
}
function sourceBreakdownText(sourceCounts){
  const sc=sourceCounts||{};
  const parts=[['live_paper','Live'],['snapshot_replay','Snapshot'],['price_replay','Price']].map(([k,l])=>`${l} ${sc[k]??0}`);
  return parts.join(' · ');
}

function minutesSince(value) {
  if (!value) return null;
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) return null;
  return (Date.now() - ms) / 60000;
}

function fundHealthView(pipe = {}) {
  const org = pipe.fund_org_summary || {};
  const summary = org.summary || {};
  const league = pipe.paper_fund_league_summary || {};
  const replay = pipe.paper_fund_historical_replay_summary || {};
  const price = pipe.paper_fund_price_replay_summary || {};
  const consensus = org.recommendation_consensus || {};
  const registered = Number(summary.registered_fund_count ?? org.performance?.fund_count ?? 0);
  const recConsensus = Number(summary.recommendation_consensus_count ?? consensus.item_count ?? consensus.items_count ?? 0);
  const riskFindings = Number(summary.risk_finding_count ?? org.risk?.finding_count ?? 0);
  const leagueFresh = minutesSince(league.run_at);
  const replayFresh = minutesSince(replay.run_at);
  const consensusFresh = minutesSince(pipe.run_at || org.run_at);
  const coreFresh = [leagueFresh, replayFresh, consensusFresh].filter((x) => x !== null);
  const isFresh = coreFresh.length ? coreFresh.every((x) => x < 180) : false;
  const ok = registered > 0 && isFresh;
  const staleBits = [];
  if (leagueFresh !== null && leagueFresh >= 180) staleBits.push('league 지연');
  if (replayFresh !== null && replayFresh >= 180) staleBits.push('historical replay 지연');
  return {
    ok,
    label: ok ? '정상' : '확인 필요',
    kind: ok ? 'good' : 'warn',
    registered,
    recConsensus,
    riskFindings,
    topFund: summary.top_fund_id || org.performance?.top_fund?.id || org.registry?.top_fund?.id || price.summary?.top_fund?.id,
    topReturn: summary.top_fund_return_pct ?? org.performance?.top_fund?.return_pct ?? org.registry?.top_fund?.return_pct ?? price.summary?.top_fund?.return_pct,
    priceRunAt: price.run_at,
    staleText: staleBits.join(' · '),
  };
}


function renderFundHoldingsTable(f){
  const hs=f.holdings||[];
  if(!hs.length) return '<div class="empty-state compact">최종 보유종목 없음</div>';
  const panelId=`holding-panel-${String(f.id||'fund').replace(/[^a-zA-Z0-9_-]/g,'_')}`;
  return `<div class="fund-detail-holdings"><table class="fund-holdings-table rich"><thead><tr><th>종목</th><th>수량</th><th>매입가</th><th>현재가</th><th>시장가치</th><th>목표/손절</th><th>전략/점수</th><th>손익</th></tr></thead><tbody>${hs.map(h=>{ const payload=encodeURIComponent(JSON.stringify({h,f:{id:f.id,style:f.style,score_min:f.score_min,target_pct:f.target_pct,stop_pct:f.stop_pct}})); const reasons=Array.isArray(h.reason)?h.reason.join(', '):(h.reason||''); const entryPlan=h.entry_plan||{}; return `<tr onclick="showHoldingReason('${payload}','${panelId}')"><td><b>${nameOf(h.symbol)||h.symbol}</b><code>${h.symbol}</code><small>${h.entry_date||'-'}</small></td><td>${h.qty}</td><td>${fmt(h.entry_price)}<br><small>FX ${fmt(h.entry_fx)}</small></td><td>${fmt(h.current_price)}<br><small>KRW ${fmt(h.current_price_krw ?? h.current_price)} · FX ${fmt(h.fx_rate)}</small></td><td>${fmt(h.market_value)}원</td><td>${fmt(h.target)}<br><span class="risk">${fmt(h.stop)}</span></td><td><b>${h.strategy_role||'-'}</b><br><small>score ${fmt(h.score)} · ${reasons||entryPlan.mode||'-'}</small></td><td class="${(h.unrealized_pnl||0)>=0?'good-text':'bad-text'}">${fmt(h.unrealized_pnl)}원<br><small>${pct(h.unrealized_pnl_pct)}</small></td></tr>`; }).join('')}</tbody></table><div id="${panelId}" class="holding-inline-panel"></div></div>`;
}
function fundKeyValueList(title, rows){
  const visible=(rows||[]).filter(r=>r && r[1]!==undefined && r[1]!==null && r[1]!=='' && r[1]!=='-');
  if(!visible.length) return '';
  return `<div class="fund-kv-panel"><h4>${title}</h4><div>${visible.map(([k,v,sub])=>`<span><b>${k}</b><em>${v}</em>${sub?`<small>${sub}</small>`:''}</span>`).join('')}</div></div>`;
}
function fundObjectEntries(obj, limit=8){
  return Object.entries(obj||{}).sort((a,b)=>(Number(b[1])||0)-(Number(a[1])||0)).slice(0,limit);
}
function renderFundDataCoverage(f){
  const se=f.strategy_effectiveness||{};
  const roleRows=fundObjectEntries(se.unrealized_pnl_by_role||{},5).map(([k,v])=>[k, `${fmt(v)}원`, `보유 ${(se.holding_count_by_role||{})[k]??'-'} · mix ${(f.strategy_mix||{})[k]??0}`]);
  const mixRows=fundObjectEntries(f.strategy_mix||{},6).map(([k,v])=>[k, v, (f.allowed_strategy_roles||[]).includes(k)?'allowed':'observed']);
  const profileRows=[
    ['세대/나이', `G${f.generation??'-'} · ${f.age_days??'-'}d`, f.parent_id?`parent ${f.parent_id}`:'root'],
    ['현금버퍼', pct((f.cash_buffer||0)*100), `1회 위험 ${pct((f.risk_per_trade||0)*100)}`],
    ['최대 종목노출', pct((f.max_symbol_exposure_pct||0)*100), `score min ${fmt(f.score_min)}`],
    ['전략 정렬', pct(se.alignment_pct), `dominant ${se.dominant_role||'-'} ${pct(se.dominant_role_share_pct)}`],
    ['비용합계', `${fmt(f.total_costs)}원`, `source ${fundSourceLabel(f.source)}`],
  ];
  const panels=[fundKeyValueList('운용 프로파일', profileRows), fundKeyValueList('전략 PnL/보유', roleRows), fundKeyValueList('전략 Mix', mixRows)].filter(Boolean).join('');
  return panels ? `<div class="fund-data-coverage">${panels}</div>` : '';
}
function renderSelectedFundDetail(f){
  if(!f) return '<div class="empty-state">Fund 자산 목록에서 항목을 선택하세요.</div>';
  const rules=[f.pyramid_enabled?'불타기':null,f.average_down_enabled?'물타기':null,f.scale_out_enabled?'부분익절':null,f.trailing_stop_enabled?'추적손절':null].filter(Boolean).join(' · ') || '기본 매수/청산';
  return `<section class="selected-fund-detail"><div class="selected-fund-head"><div><h3>${f.id}</h3><p>${f.style||'-'} · ${fundStyleDescription(f.style)}</p><em>${fundSourceLabel(f.source)} · ${fundAssetNote(f)}</em></div><div class="fund-detail-head-actions"><span class="badge good">${f.tier||'fund'}</span><button class="fund-detail-close" type="button" onclick="closeFundDetail()" title="상세 패널 숨김">×</button></div></div>
    <div class="selected-fund-kpis">
      ${compactStat('현재자산', fundCurrentAsset(f)!==null ? `${fmt(fundCurrentAsset(f))}원` : '-', `${pct(f.return_pct)} · ${fundAssetNote(f)}`)}
      ${compactStat('최대낙폭(MDD)', pct(f.mdd_pct), `비용 ${fmt(f.total_costs)}원`)}
      ${compactStat('보유/거래', `${f.position_count??'-'} / ${f.trade_count??'-'}`, `품질 ${fmt(f.fund_quality_score)}`)}
      ${compactStat('운용룰', rules, `목표 ${pct(f.target_pct)} / 손절 ${pct(f.stop_pct)}`)}
    </div>
    <div class="readable-reason"><b>Fund 요약</b><span>${fundStyleDescription(f.style)}. 현금버퍼 ${pct((f.cash_buffer||0)*100)}, 1회 위험비중 ${pct((f.risk_per_trade||0)*100)}, 최소 점수 ${fmt(f.score_min)} 기준으로 운용됩니다.</span></div>
    ${renderFundDataCoverage(f)}
    <div class="selected-fund-section-head"><h4>최종 보유종목</h4><button class="button secondary fund-trades-btn" data-fund-id="${f.id}">거래이력</button></div>
    ${renderFundHoldingsTable(f)}
  </section>`;
}


const FUND_DETAIL_MIN_PX = 340;
const FUND_DETAIL_MAX_PX = 9999;
const FUND_DETAIL_EDGE_GAP_PX = 16;
const FUND_DETAIL_SPLITTER_GAP_PX = 8;
function storedFundDetailWidthPx(){
  const px = Number(localStorage.getItem('fundDetailWidthPx'));
  return Number.isFinite(px) && px > 0 ? px : null;
}
function fundDetailWidthPxForLayout(layout, preferredPct){
  const rect = layout?.getBoundingClientRect?.();
  const layoutWidth = rect?.width || 0;
  const safeWidth = Math.max(0, layoutWidth - FUND_DETAIL_EDGE_GAP_PX);
  const maxPx = Math.max(FUND_DETAIL_MIN_PX, Math.min(FUND_DETAIL_MAX_PX, safeWidth || FUND_DETAIL_MAX_PX));
  const storedPx = storedFundDetailWidthPx();
  const preferredPx = storedPx ?? (layoutWidth ? (layoutWidth * preferredPct / 100) : 520);
  return Math.min(maxPx, Math.max(FUND_DETAIL_MIN_PX, Number.isFinite(preferredPx) ? preferredPx : 520));
}
function fundDetailWidthPct(){
  const v=Number(localStorage.getItem('fundDetailWidthPct')||34);
  return Math.min(55, Math.max(26, Number.isFinite(v)?v:34));
}
function applyFundDetailWidth(layout, widthPx){
  if(!layout) return;
  const rect = layout.getBoundingClientRect();
  const layoutWidth = rect.width || 1;
  const clampedPx = fundDetailWidthPxForLayout(layout, widthPx / layoutWidth * 100);
  layout.style.setProperty('--fund-detail-width-px', clampedPx.toFixed(0)+'px');
  layout.style.setProperty('--fund-detail-splitter-right-px', (clampedPx + FUND_DETAIL_SPLITTER_GAP_PX).toFixed(0)+'px');
  layout.style.setProperty('--fund-detail-width', (clampedPx / layoutWidth * 100).toFixed(1)+'%');
  localStorage.setItem('fundDetailWidthPx', clampedPx.toFixed(0));
  localStorage.setItem('fundDetailWidthPct', (clampedPx / layoutWidth * 100).toFixed(1));
}
function closeFundDetail(){
  window.__selectedFundId=null;
  const layout=document.querySelector('.fund-engine-clean-layout');
  if(layout){ layout.classList.remove('is-detail-open'); layout.style.removeProperty('--fund-detail-width'); }
  document.querySelectorAll('.fund-league-row.clean').forEach(x=>x.classList.remove('selected'));
  setHtml('selected-fund-detail','<div class="asset-detail-empty">Fund 행을 선택하면 상세정보 패널이 열립니다.</div>');
}
function initFundDetailResize(){
  const layout=document.querySelector('.fund-engine-clean-layout');
  const splitter=document.querySelector('.fund-asset-splitter');
  if(!layout||!splitter) return;
  applyFundDetailWidth(layout, fundDetailWidthPxForLayout(layout, fundDetailWidthPct()));
  let dragging=false;
  const move=(ev)=>{
    if(!dragging) return;
    ev.preventDefault?.();
    const rect=layout.getBoundingClientRect();
    if(!rect.width) return;
    const x=(ev.touches?.[0]?.clientX ?? ev.clientX);
    const detailPx=rect.right - x;
    applyFundDetailWidth(layout, detailPx);
  };
  const stop=()=>{ dragging=false; splitter.classList.remove('is-dragging'); document.body.classList.remove('fund-resizing'); };
  splitter.onmousedown=(ev)=>{ dragging=true; splitter.classList.add('is-dragging'); document.body.classList.add('fund-resizing'); ev.preventDefault(); };
  splitter.ontouchstart=(ev)=>{ dragging=true; splitter.classList.add('is-dragging'); document.body.classList.add('fund-resizing'); ev.preventDefault(); };
  document.addEventListener('mousemove',move);
  document.addEventListener('touchmove',move,{passive:false});
  document.addEventListener('mouseup',stop);
  document.addEventListener('touchend',stop);
}

async function renderFundLeaderboard(){
  const cards=document.getElementById('fund-performance-cards'); if(!cards) return;
  try{
    const fundPerf=await apiWithStaticFallback('/api/research/fund/performance/latest','/static/fund_performance_evaluator_latest.json',null);
    const evals=(fundPerf?.evaluations)||[];
    if(!evals.length) return;
    const sourceCounts=evals.reduce((acc,f)=>{ const k=f.source||'unknown'; acc[k]=(acc[k]||0)+1; return acc; },{});
    const priceRows=evals.filter(f=>f.source==='price_replay');
    const leagueBase=priceRows.length ? priceRows : evals;
    const sorted=[...leagueBase].sort((a,b)=>(b.return_pct||0)-(a.return_pct||0)).slice(0,40);
    window.__fundLeagueData=sorted;
    const selectedId=window.__selectedFundId && sorted.find(f=>f.id===window.__selectedFundId) ? window.__selectedFundId : null;
    window.__selectedFundId=selectedId;
    const rows=sorted.map((f,idx)=>`<button class="fund-league-row clean ${f.id===selectedId?'selected':''}" data-fund-id="${f.id}"><span class="rank">#${idx+1}</span><span class="fund-id"><b>${f.id}</b><em>${fundSourceLabel(f.source)} · ${f.style||'-'}</em></span><span class="asset"><b>${fundCurrentAsset(f)!==null?fmt(fundCurrentAsset(f))+'원':'-'}</b><em>${pct(f.return_pct)} · ${fundAssetNote(f)}</em></span><span>${pct(f.mdd_pct)}</span><span>${f.position_count??'-'} / ${f.trade_count??'-'}</span><span class="rules">${[f.pyramid_enabled?'불타기':null,f.average_down_enabled?'물타기':null,f.scale_out_enabled?'부분익절':null,f.trailing_stop_enabled?'추적손절':null].filter(Boolean).join(' · ') || '기본'}</span><span class="badge good">${f.tier||'fund'}</span></button>`).join('');
    const selected=selectedId ? sorted.find(f=>f.id===selectedId) : null;
    setHtml('fund-performance-cards', `<div class="fund-engine-clean-layout ${selected?'is-detail-open':''}" style="--fund-detail-width:${fundDetailWidthPct()}%;--fund-detail-width-px:${storedFundDetailWidthPx()||'520'}px;--fund-detail-splitter-right-px:${(storedFundDetailWidthPx()||520)+FUND_DETAIL_SPLITTER_GAP_PX}px"><section class="fund-league-panel clean"><div class="fund-league-head"><h3>Fund 자산 인벤토리</h3><p>성과순 표시: ${priceRows.length?'Price replay':'전체'} ${sorted.length}개 · 등록 ${evals.length}개 (${sourceBreakdownText(sourceCounts)})</p></div><div class="fund-league-header"><span>순위</span><span>Fund</span><span>평가자산(수익률)</span><span>MDD</span><span>보유/거래</span><span>운용 프로파일</span><span>Tier</span></div><div class="fund-league-list clean">${rows}</div></section><div class="fund-asset-splitter" aria-hidden="true"></div><aside id="selected-fund-detail" class="fund-detail-dock">${selected ? renderSelectedFundDetail(selected) : '<div class="asset-detail-empty">Fund 행을 선택하면 상세정보 패널이 열립니다.</div>'}</aside></div>`);
    initFundDetailResize();
    document.querySelectorAll('.fund-league-row.clean').forEach(btn=>btn.addEventListener('click',()=>{
      window.__selectedFundId=btn.dataset.fundId;
      document.querySelectorAll('.fund-league-row.clean').forEach(x=>x.classList.toggle('selected',x.dataset.fundId===window.__selectedFundId));
      const layout=document.querySelector('.fund-engine-clean-layout');
      if(layout){ layout.classList.add('is-detail-open'); applyFundDetailWidth(layout, fundDetailWidthPxForLayout(layout, fundDetailWidthPct())); }
      const f=(window.__fundLeagueData||[]).find(x=>x.id===window.__selectedFundId);
      setHtml('selected-fund-detail', renderSelectedFundDetail(f));
    }));
  }catch(e){ console.warn('fund leaderboard failed', e); }
}
async function renderFundExtendedSummary(){
  const target=document.getElementById('fund-performance-extra');
  if(!target) return;
  try{
    const [perf, consensus, recConsensus, trades]=await Promise.all([
      apiWithStaticFallback('/api/research/fund/performance/latest','/static/fund_performance_evaluator_latest.json',{}),
      apiWithStaticFallback('/api/research/fund/consensus/latest','/static/fund_consensus_latest.json',{}),
      apiWithStaticFallback('/api/research/fund/recommendation-consensus/latest','/static/fund_recommendation_consensus_latest.json',{}),
      apiWithStaticFallback('/api/research/fund/trades/latest','/static/fund_trade_history_latest.json',{}),
    ]);
    const summary=perf.summary||{};
    const evals=perf.evaluations||[];
    const topFund=summary.top_fund || evals[0] || null;
    const sourceCounts=evals.reduce((acc,f)=>{ const k=f.source||'unknown'; acc[k]=(acc[k]||0)+1; return acc; },{});
    const recItems=recConsensus.items||[];
    const styleText=Object.entries((consensus.summary||{}).top_styles || {}).map(([k,v])=>`${k} ${v}`).join(' · ') || '-';
    setHtml('fund-performance-summary', `Fund 상태: ${evals.length ? '정상' : '확인 필요'}. 최신 evaluator 기준 ${evals.length}개 fund를 평가했습니다. 추천 consensus ${recItems.length}개 · 우세 스타일: ${styleText}.`);
    setHtml('fund-performance-metrics', `
      <div class="metric-card good"><div class="metric-label">Fund 상태</div><div class="metric-value">${evals.length ? '정상' : '확인 필요'}</div><div class="metric-label">warnings ${(perf.warnings||[]).length}</div></div>
      <div class="metric-card"><div class="metric-label">Registered Funds</div><div class="metric-value">${summary.fund_count ?? evals.length}</div><div class="metric-label">${sourceBreakdownText(sourceCounts)}</div></div>
      <div class="metric-card"><div class="metric-label">Champions</div><div class="metric-value">${summary.champion_count ?? '-'}</div><div class="metric-label">상위 운용체</div></div>
      <div class="metric-card"><div class="metric-label">Candidates</div><div class="metric-value">${summary.candidate_count ?? '-'}</div><div class="metric-label">후보 운용체</div></div>
      <div class="metric-card"><div class="metric-label">Top Fund Return</div><div class="metric-value">${pct(topFund?.return_pct)}</div><div class="metric-label">${topFund?.id || '-'} · ${topFund?.style || '-'}</div></div>
      <div class="metric-card"><div class="metric-label">추천 Consensus</div><div class="metric-value">${recItems.length}</div><div class="metric-label">symbol overlay</div></div>
      <div class="metric-card"><div class="metric-label">전략 역할</div><div class="metric-value">${(perf.strategy_role_quality||[]).length}</div><div class="metric-label">style-role ${(perf.style_strategy_role_quality||[]).length}</div></div>
      <div class="metric-card"><div class="metric-label">Price Replay</div><div class="metric-value">${evals.filter(f=>f.source==='price_replay').length}</div><div class="metric-label">price replay funds</div></div>`);
    const cost=trades.cost_model||{};
    const roleRows=(perf.strategy_role_quality||[]).slice(0,6).map(x=>[x.strategy_role, `${pct(x.avg_return_pct)} · hit ${pct(x.positive_rate_pct)}`, `fund ${x.fund_count} · trades ${x.trade_count}`]);
    const styleRows=(perf.style_strategy_role_quality||[]).slice(0,6).map(x=>[`${x.style} / ${x.strategy_role}`, `${pct(x.avg_return_pct)} · hit ${pct(x.positive_rate_pct)}`, `fund ${x.fund_count} · trades ${x.trade_count}`]);
    const consensusRows=(consensus.symbol_consensus||[]).slice(0,8).map(x=>[x.symbol, `votes ${x.votes} · score ${fmt(x.weighted_score)}`, (x.funds||[]).slice(0,3).join(', ')]);
    const recRows=(recConsensus.items||[]).slice(0,6).map(x=>[x.symbol, `${x.action_label||x.recommendation_bucket||'-'} · score ${fmt(x.score||x.weighted_score)}`, `buy ${x.buy_fund_count??'-'} · holding ${x.holding_fund_count??'-'}`]);
    const costRows=[
      ['KR 수수료/세금', `${fmt(cost.kr_commission_bps)}bp / ${fmt(cost.kr_sell_tax_bps)}bp`, '국내 매도세 포함'],
      ['US 수수료/세금', `${fmt(cost.us_commission_bps)}bp / ${fmt(cost.us_sell_fee_bps)}bp`, cost.us_gain_tax_note],
      ['진입/청산 모델', cost.entry_execution_model || cost.exit_price_logic, cost.position_management],
      ['수량/점수', cost.quantity_logic, cost.score_normalization],
    ];
    const next=(perf.next_actions||[]).concat(consensus.next_actions||[], recConsensus.next_actions||[]).slice(0,5);
    const warnings=(perf.warnings||[]).concat(consensus.warnings||[], recConsensus.warnings||[]).slice(0,5);
    setHtml('fund-performance-extra', `
      ${fundKeyValueList('Top 전략 역할 품질', roleRows)}
      ${fundKeyValueList('Style x Strategy 품질', styleRows)}
      ${fundKeyValueList('Symbol consensus', consensusRows)}
      ${fundKeyValueList('추천 consensus', recRows)}
      ${fundKeyValueList('비용/체결 모델', costRows)}
      ${fundKeyValueList('운영 메모', [
        ['Top fund', summary.top_fund?.id || summary.top_fund || '-', summary.top_fund?.style],
        ['역할 품질', `${(perf.strategy_role_quality||[]).length} roles`, `${(perf.style_strategy_role_quality||[]).length} style-role`],
        ...warnings.slice(0,3).map((w,idx)=>[`Warning ${idx+1}`, String(w), '']),
        ...next.slice(0,4).map((n,idx)=>[`Next ${idx+1}`, String(n), '']),
      ])}`);
  }catch(e){ console.warn('fund extended summary failed', e); }
}

async function renderFundTradeHistory(filter=''){
  const table=document.getElementById('fund-trade-table'); if(!table) return;
  const data=await apiWithStaticFallback('/api/research/fund/trades/latest','/static/fund_trade_history_latest.json',{items:[]});
  const rows=(data.items||[]).filter(t=>!filter || String(t.fund_id||'').toLowerCase().includes(filter.toLowerCase())).slice().reverse().slice(0,300);
  setHtml('fund-trade-summary', `${filter?filter+' · ':''}${rows.length}건 표시 · source ${data.source||'-'} · paper-only`);
  renderTable('fund-trade-table',['Date','Fund','Symbol','Side','Price','Reason/PnL'],rows,(t)=>`<tr><td>${t.date||'-'}</td><td>${t.fund_id||'-'}</td><td>${nameOf(t.symbol)}</td><td>${t.side}</td><td>${fmt(t.price)}</td><td>${t.reason||''}${t.pnl!==undefined?'<br>PnL '+fmt(t.pnl):''}</td></tr>`);
}
function openFundTrades(fid){
  switchMonitorTab('details');
  document.querySelectorAll('.inner-tab').forEach(el=>el.classList.toggle('active', el.dataset.detailTab==='fund-trades'));
  document.querySelectorAll('.detail-tab-panel').forEach(el=>el.classList.toggle('active', el.dataset.detailTabPanel==='fund-trades'));
  const input=document.getElementById('fund-trade-filter'); if(input) input.value=fid||'';
  renderFundTradeHistory(fid||'');
}

function renderFundPerformancePanel(pipe = {}, recommendations = {}) {
  if (!document.getElementById('fund-performance-summary')) return;
  const org = pipe.fund_org_summary || {};
  const perf = org.performance || {};
  const registry = org.registry || {};
  const risk = org.risk || {};
  const consensus = org.consensus || {};
  const priceReplay = pipe.paper_fund_price_replay_summary || {};
  const health = fundHealthView(pipe);
  const topFund = perf.top_fund || registry.top_fund || priceReplay.summary?.top_fund || null;
  const recItems = sortRecommendations(recommendations.items || []);
  const fundOverlayScore = (r) => {
    const basis = r.validation_basis || {};
    const styleBoost = Number(basis.fund_style_consensus_boost_total || 0);
    const recommendationBoost = Number(basis.fund_recommendation_score_boost || 0);
    const consensusBoost = Number(basis.fund_consensus_score_boost || 0);
    const recommendationVotes = Number((basis.fund_recommendation_consensus || {}).buy_fund_count || 0);
    const styleVotes = Object.values(basis.fund_style_consensus || {}).reduce((sum, v) => sum + Number(v || 0), 0);
    return styleBoost + recommendationBoost + consensusBoost + recommendationVotes * 0.01 + styleVotes * 0.001;
  };
  const fundLinked = recItems.filter((r) => fundOverlayScore(r) > 0);
  const visible = (fundLinked.length ? fundLinked : recItems).slice(0, 8);
  const styleText = Object.entries(consensus.top_styles || {}).map(([k,v]) => `${k} ${v}`).join(' · ') || (Array.isArray(registry.top_styles) ? registry.top_styles.join(', ') : '-');
  setHtml('fund-performance-summary', `Fund 상태: ${health.label}${health.staleText ? ` (${health.staleText})` : ''}. 종목추천이 1차 목적이고, Fund는 추천 품질을 높이는 평가/선별 엔진입니다. 우세 스타일: ${styleText}.`);
  setHtml('fund-performance-metrics', `
    <div class="metric-card ${health.kind}"><div class="metric-label">Fund 상태</div><div class="metric-value">${health.label}</div><div class="metric-label">추천합의 ${health.recConsensus} · risk ${health.riskFindings}</div></div>
    <div class="metric-card"><div class="metric-label">Registered Funds</div><div class="metric-value">${perf.fund_count ?? '-'}</div><div class="metric-label">${sourceBreakdownText((org.registry||{}).source_counts)}</div></div>
    <div class="metric-card"><div class="metric-label">Champions</div><div class="metric-value">${perf.champion_count ?? '-'}</div><div class="metric-label">상위 운용체</div></div>
    <div class="metric-card"><div class="metric-label">Candidates</div><div class="metric-value">${perf.candidate_count ?? '-'}</div><div class="metric-label">후보 운용체</div></div>
    <div class="metric-card"><div class="metric-label">Top Fund Return</div><div class="metric-value">${pct(topFund?.return_pct)}</div><div class="metric-label">${topFund?.style || '-'}</div></div>
    <div class="metric-card"><div class="metric-label">Risk Findings</div><div class="metric-value">${risk.finding_count ?? 0}</div><div class="metric-label">guardian</div></div>
    <div class="metric-card"><div class="metric-label">추천 반영</div><div class="metric-value">${fundLinked.length}/${recItems.length}</div><div class="metric-label">fund overlay evidence</div></div>
    <div class="metric-card"><div class="metric-label">Price Replay</div><div class="metric-value">${priceReplay.trading_days ?? '-'}</div><div class="metric-label">trading days</div></div>`);
  const cards=[];
  if (topFund) cards.push(`<article class="audit-card good"><div class="audit-card-top"><strong>Top Fund · ${topFund.id || '-'}</strong><span class="badge good">${topFund.tier || 'champion'}</span></div><div class="audit-sub">${topFund.style || '-'} · age ${topFund.age_days ?? '-'}d · generation ${topFund.generation ?? '-'}</div><div class="strategy-metrics"><div><span>Return</span><b>${pct(topFund.return_pct)}</b></div><div><span>MDD</span><b>${pct(topFund.mdd_pct)}</b></div><div><span>Trades</span><b>${topFund.trade_count ?? '-'}</b></div><div><span>Quality</span><b>${fmt(topFund.fund_quality_score)}</b></div></div><div class="audit-foot">source: ${topFund.source || 'price_replay'} · price replay ${priceReplay.trading_days ?? '-'} trading days</div></article>`);
  cards.push(`<article class="audit-card neutral"><div class="audit-card-top"><strong>Fund → 추천 종목 연결</strong><span class="badge neutral">overlay</span></div><div class="audit-sub">상위 fund 신규매수/보유/스타일 합의가 추천 근거에 반영된 종목 ${fundLinked.length}개</div><div class="audit-foot">${visible.map((r) => { const basis = r.validation_basis || {}; const fundRec = basis.fund_recommendation_consensus || {}; const priority = basis.fund_recommendation_priority || {}; const boost = Number(basis.fund_recommendation_score_boost || 0) + Number(basis.fund_consensus_score_boost || 0) + Number(basis.fund_style_consensus_boost_total || 0); return `<b>${companyNameOf(r)} ${r.symbol}</b>: ${r.recommendation_bucket_label || r.action_label || r.action || '-'} · score ${fmt(r.score)} · fund boost ${fmt(boost)} · buy funds ${fundRec.buy_fund_count ?? 0} · rank ${priority.rank ?? '-'}`; }).join('<br>') || '표시할 추천 후보 없음'}</div></article>`);
  const recCards = visible.slice(0,4).map((r,idx)=>renderRecommendationCard(r,idx)).join('');
  if (recCards) cards.push(`<article class="audit-card neutral fund-linked-recommendations"><div class="audit-card-top"><strong>추천 종목 미리보기</strong><span class="badge neutral">fund linked</span></div><div class="recommendation-card-list embedded-recs">${recCards}</div></article>`);
  // Fund cards are rendered by renderFundLeaderboard() as the canonical asset-grid view.
  // Do not write the legacy summary-card view here; it causes a visible flash on refresh.
  renderFundExtendedSummary();
  if (!document.querySelector('#fund-performance-cards .fund-engine-clean-layout')) {
    setHtml('fund-performance-cards', '<div class="empty-state compact">Fund 자산 인벤토리를 불러오는 중입니다.</div>');
  }
}

function renderFundOrgSummary(pipe = {}) {
  const org = pipe.fund_org_summary || {};
  const registry = org.registry || {};
  const perf = org.performance || {};
  const risk = org.risk || {};
  const consensus = org.consensus || {};
  const priceReplay = pipe.paper_fund_price_replay_summary || {};
  const health = fundHealthView(pipe);
  const topFund = perf.top_fund || registry.top_fund || priceReplay.summary?.top_fund || null;
  const topStyles = consensus.top_styles || registry.top_styles || {};
  const sourceCounts = registry.source_counts || {};
  const hasData = !!(topFund || perf.fund_count || registry.source_counts || priceReplay.summary);
  if (!document.getElementById('fund-org-summary')) return;
  if (!hasData) {
    setHtml('fund-org-summary', '아직 최신 pipeline summary에 fund 조직 데이터가 반영되지 않았습니다. 다음 pipeline run 이후 자동 표시됩니다.');
    setHtml('fund-org-metrics', '');
    setHtml('fund-org-cards', '<div class="empty-state">fund_registry / fund_performance_evaluator / fund_consensus 실행 결과 대기 중</div>');
    return;
  }
  const styleText = Array.isArray(topStyles) ? topStyles.join(', ') : Object.entries(topStyles).map(([k,v]) => `${k} ${v}`).join(' · ');
  setHtml('fund-org-summary', `Fund 상태: ${health.label}${health.staleText ? ` (${health.staleText})` : ''}. Fund는 전략 DNA/도구를 묶어 평가하는 엔진이고, 추천은 상위 fund 합의와 risk guardian을 overlay로 사용합니다.${styleText ? ` 현재 우세 스타일: ${styleText}.` : ''}`);
  setHtml('fund-org-metrics', `
    <div class="metric-card ${health.kind}"><div class="metric-label">Fund 상태</div><div class="metric-value">${health.label}</div><div class="metric-label">대표 ${health.topFund || '-'} · ${pct(health.topReturn)}</div></div>
    <div class="metric-card"><div class="metric-label">Registered Funds</div><div class="metric-value">${perf.fund_count ?? (Object.values(sourceCounts).reduce((a,b)=>a+(Number(b)||0),0) || '-')}</div><div class="metric-label">${sourceBreakdownText(sourceCounts)}</div></div>
    <div class="metric-card"><div class="metric-label">Champions</div><div class="metric-value">${perf.champion_count ?? '-'}</div><div class="metric-label">quality tier</div></div>
    <div class="metric-card"><div class="metric-label">Candidates</div><div class="metric-value">${perf.candidate_count ?? '-'}</div><div class="metric-label">next allocation pool</div></div>
    <div class="metric-card"><div class="metric-label">Risk Findings</div><div class="metric-value">${risk.finding_count ?? 0}</div><div class="metric-label">MDD/turnover guard</div></div>
    <div class="metric-card"><div class="metric-label">Top-Fund Consensus</div><div class="metric-value">${consensus.top_fund_count ?? '-'}</div><div class="metric-label">symbol ${consensus.symbol_consensus_count ?? 0}</div></div>
    <div class="metric-card"><div class="metric-label">Price Replay</div><div class="metric-value">${priceReplay.trading_days ?? '-'}</div><div class="metric-label">trading days</div></div>`);
  const cards = [];
  if (topFund) cards.push(`<article class="audit-card good"><div class="audit-card-top"><strong>대표 Fund: ${topFund.id || '-'}</strong><span class="badge good">${topFund.tier || 'top'}</span></div><div class="audit-sub">${topFund.style || '-'} · generation ${topFund.generation ?? '-'} · age ${topFund.age_days ?? '-'}d</div><div class="strategy-metrics"><div><span>Return</span><b>${pct(topFund.return_pct)}</b></div><div><span>MDD</span><b>${pct(topFund.mdd_pct)}</b></div><div><span>Trades</span><b>${topFund.trade_count ?? '-'}</b></div><div><span>Quality</span><b>${fmt(topFund.fund_quality_score)}</b></div></div><div class="audit-foot">source: ${topFund.source || 'price_replay'} · target ${pct(topFund.target_pct)} · stop ${pct(topFund.stop_pct)}</div></article>`);
  cards.push(`<article class="audit-card neutral"><div class="audit-card-top"><strong>조직 역할 전환</strong><span class="badge neutral">Fund-first</span></div><div class="audit-sub">Fund Registry → Performance Evaluator → Risk Guardian → Fund Consensus → Recommendation Overlay</div><div class="audit-foot"><b>변경점</b>: active 전략 자체를 관리하기보다, fund의 성향/파라미터/전략 조합을 평가·은퇴·복제합니다.<br><b>위원회</b>: 개별 추천 찬반보다 fund-level risk/allocation guard로 축소.</div></article>`);
  cards.push(`<article class="audit-card neutral"><div class="audit-card-top"><strong>상위 Fund 스타일 합의</strong><span class="badge neutral">DNA</span></div><div class="audit-sub">${styleText || '아직 style consensus 없음'}</div><div class="audit-foot">추천 엔진은 이 합의를 strategy family boost로 사용합니다. symbol consensus는 live holdings가 충분히 쌓이면 표시됩니다.</div></article>`);
  setHtml('fund-org-cards', cards.join(''));
}


async function renderFundOrgStaticFallback(pipe = {}) {
  if (pipe.fund_org_summary || !document.getElementById('fund-org-summary')) return;
  try {
    const fallback = await apiWithStaticFallback('/api/research/fund/org/latest','/static/fund_suborg_summary_latest.json',null) || await apiWithStaticFallback('/api/research/fund/org/latest','/static/fund_org_summary_latest.json',null);
    if (fallback) renderFundOrgSummary(fallback);
  } catch (_) {}
}

function avgNum(rows, key) {
  const vals = (rows || []).map((r) => Number(r?.[key])).filter((v) => Number.isFinite(v));
  return vals.length ? vals.reduce((a,b)=>a+b,0) / vals.length : null;
}

function fmtDateTime(value) {
  if (!value) return '-';
  const ms = typeof value === 'number' ? (value > 1e12 ? value : value * 1000) : Date.parse(value);
  return Number.isFinite(ms) ? new Date(ms).toLocaleString() : '-';
}

function fmtDurationSec(sec) {
  const n = Number(sec);
  if (!Number.isFinite(n)) return '-';
  if (n < 0) return '곧 실행';
  if (n < 90) return `${Math.round(n)}초`;
  if (n < 3600) return `${Math.round(n / 60)}분`;
  return `${(n / 3600).toFixed(1)}시간`;
}

function renderValidationWorkerStatus(workerStatus = {}) {
  const worker = workerStatus.worker || (workerStatus.last_run_at || workerStatus.status ? workerStatus : {});
  const capacity = workerStatus.capacity || {};
  const sim = workerStatus.simulation || {};
  const current = workerStatus.current_recommendation || {};
  const age = worker.age_sec ?? workerStatus.age_sec;
  const isFresh = Number.isFinite(Number(age)) ? Number(age) < 600 : false;
  const statusLabel = worker.status === 'ok' && isFresh ? '정상 순환' : (worker.status || 'unknown');
  let inferredNextRunAt = workerStatus.next_run_at || null;
  if (!inferredNextRunAt && (worker.mtime || worker.last_run_at) && (worker.cadence_recommendation || capacity.cadence_recommendation)) {
    const baseMs = worker.mtime ? worker.mtime * 1000 : Date.parse(worker.last_run_at);
    const cad = String(worker.cadence_recommendation || capacity.cadence_recommendation || '');
    const amount = parseInt(cad, 10);
    const durMs = cad.endsWith('m') ? amount * 60000 : amount * 1000;
    if (Number.isFinite(baseMs) && Number.isFinite(durMs)) inferredNextRunAt = baseMs + durMs;
  }
  const inferredNextIn = workerStatus.next_run_in_sec ?? (inferredNextRunAt ? (inferredNextRunAt - Date.now()) / 1000 : null);
  const nextRun = inferredNextRunAt ? fmtDateTime(inferredNextRunAt) : '-';
  const nextIn = inferredNextIn == null ? '-' : fmtDurationSec(inferredNextIn);
  setHtml('validation-worker-summary', `검증 워커 ${statusLabel} · 최근 실행 ${fmtDateTime(worker.last_run_at || worker.mtime)} · 다음 예상 ${nextRun} (${nextIn}) · batch ${worker.processed_combinations ?? sim.processed_combinations ?? '-'}건 · backlog ${fmt(worker.pending_results_estimate ?? capacity.pending_results_estimate)}`);
  setHtml('validation-worker-metrics', `
    <div class="metric-card verdict ${worker.status || ''}"><div class="metric-label">워커 상태</div><div class="metric-value">${statusLabel}</div><div class="metric-label">실패 ${worker.consecutive_failures ?? 0}</div></div>
    <div class="metric-card"><div class="metric-label">마지막 실행</div><div class="metric-value small-text">${fmtDateTime(worker.last_run_at || worker.mtime)}</div><div class="metric-label">${fmtDurationSec(age)} 전</div></div>
    <div class="metric-card"><div class="metric-label">다음 예정</div><div class="metric-value small-text">${nextRun}</div><div class="metric-label">${nextIn} 후 · ${worker.cadence_recommendation || capacity.cadence_recommendation || '-'}</div></div>
    <div class="metric-card"><div class="metric-label">최근 Batch</div><div class="metric-value">${worker.processed_combinations ?? sim.processed_combinations ?? '-'}</div><div class="metric-label">inserted ${worker.saved?.inserted ?? sim.saved?.inserted ?? 0} · skipped ${worker.saved?.skipped ?? sim.saved?.skipped ?? 0}</div></div>
    <div class="metric-card"><div class="metric-label">백로그</div><div class="metric-value small-text">${fmt(worker.pending_results_estimate ?? capacity.pending_results_estimate)}</div><div class="metric-label">coverage ${pct(worker.coverage_pct ?? capacity.coverage_pct)}</div></div>
    <div class="metric-card"><div class="metric-label">추천 검증</div><div class="metric-value">${current.batch_size ?? sim.processed_combinations ?? '-'}</div><div class="metric-label">symbols ${(current.symbols || []).length || '-'} · logics ${(current.logics || []).length || '-'}</div></div>
  `);
}

function renderValidationMain(pipe, recommendations, recommendationOutcomes, recommendationAudit, candidateFunnel, recommendationCalibration, validationSummary, validationWorkerStatus) {
  const outcomeSummary = recommendationOutcomes.summary || {};
  const rows = recommendationOutcomes.items || [];
  const completed = rows.filter((r) => r.status === 'complete' || r.status === 'stopped_out');
  const pending = rows.filter((r) => r.status === 'pending');
  const latestDate = rows.map((r) => r.trade_date || (r.run_at || '').slice(0,10)).filter(Boolean).sort().pop();
  const dailyRows = latestDate ? rows.filter((r) => (r.trade_date || (r.run_at || '').slice(0,10)) === latestDate) : rows.slice(0, 0);
  const dailyCompleted = dailyRows.filter((r) => r.status === 'complete' || r.status === 'stopped_out');
  const dailyPending = dailyRows.filter((r) => r.status === 'pending');
  const dailyHits = dailyCompleted.filter((r) => r.hit === true).length;
  const dailyMisses = dailyCompleted.filter((r) => r.hit === false).length;
  const dailyHitRate = dailyCompleted.length ? dailyHits / dailyCompleted.length * 100 : null;
  const dailyAvgReturn = avgNum(dailyCompleted, 'forward_return_pct');
  const dailyAvgExcess = avgNum(dailyCompleted, 'excess_return_pct');
  const sevenDates = [...new Set(rows.map((r) => r.trade_date || (r.run_at || '').slice(0,10)).filter(Boolean))].sort().slice(-7);
  const sevenRows = rows.filter((r) => sevenDates.includes(r.trade_date || (r.run_at || '').slice(0,10)));
  const sevenCompleted = sevenRows.filter((r) => r.status === 'complete' || r.status === 'stopped_out');
  const sevenHitRate = sevenCompleted.length ? sevenCompleted.filter((r) => r.hit === true).length / sevenCompleted.length * 100 : null;
  const sevenAvgExcess = avgNum(sevenCompleted, 'excess_return_pct');
  const funnelSummary = candidateFunnel.summary || {};
  const calibrationSummary = recommendationCalibration.summary || {};
  const targetReturnEval = pipe.target_return_adjustment_evaluation_summary || {};
  const supplyWeightEval = pipe.supply_weight_evaluation_summary || {};
  renderValidationWorkerStatus(validationWorkerStatus || {});
  const blockers = [];
  if ((pipe.recommendations_summary?.trade_eligible_count ?? 0) === 0) blockers.push('trade eligible 0');
  if ((funnelSummary.critic_high || 0) > 0) blockers.push(`critic high ${funnelSummary.critic_high}`);
  if ((validationSummary.best?.quality_grade || recommendationAudit.summary?.best?.quality_grade) === 'low') blockers.push('audit quality low');
  setHtml('validation-daily-summary', `최신 일자 ${latestDate || '-'} · paper/historical canonical 성과 ${dailyRows.length}개 · 완료 ${dailyCompleted.length}개 · 대기 ${dailyPending.length}개${blockers.length ? ` · 주요 blocker: ${blockers.slice(0,3).join(', ')}` : ''}`);
  setHtml('validation-daily-metrics', `
    <div class="metric-card"><div class="metric-label">일일 Hit</div><div class="metric-value">${dailyHitRate == null ? '-' : pct(dailyHitRate)}</div><div class="metric-label">${dailyHits} hit / ${dailyMisses} miss</div></div>
    <div class="metric-card"><div class="metric-label">일일 평균수익</div><div class="metric-value">${pct(dailyAvgReturn)}</div><div class="metric-label">초과 ${pct(dailyAvgExcess)}</div></div>
    <div class="metric-card"><div class="metric-label">대기</div><div class="metric-value">${dailyPending.length}</div><div class="metric-label">완료 ${dailyCompleted.length}</div></div>
    <div class="metric-card"><div class="metric-label">최근 7일 Hit</div><div class="metric-value">${sevenHitRate == null ? '-' : pct(sevenHitRate)}</div><div class="metric-label">초과 ${pct(sevenAvgExcess)} · n=${sevenCompleted.length}</div></div>
    <div class="metric-card"><div class="metric-label">Bucket 변화</div><div class="metric-value">${recommendations?.changes?.change_count ?? pipe.recommendations_summary?.bucket_change_count ?? '-'}</div><div class="metric-label">post-committee 포함</div></div>
    <div class="metric-card"><div class="metric-label">Trade Eligible</div><div class="metric-value">${pipe.recommendations_summary?.trade_eligible_count ?? 0}</div><div class="metric-label">추천 ${pipe.recommendations_summary?.item_count ?? '-'}</div></div>
  `);
  const targetArms = (targetReturnEval.summary?.arm_sample_backlog || []).slice(0,5);
  const supplyBuckets = supplyWeightEval.summary?.by_supply_adjustment_bucket || {};
  const auditBest = validationSummary.best || recommendationAudit.summary?.best || {};
  renderCards('validation-main-cards', [
    {kind:'audit', title:'전략 신뢰/국면 라벨', body:`best ${validationSummary.best_logic || recommendationAudit.summary?.best_logic || '-'} · ${auditBest.role_label || auditBest.best_use || auditBest.quality_grade || '조건 라벨 검토'}`, foot:`${(auditBest.quality_flags || []).slice(0,3).map((f)=>auditFlagLabel(f,false)).join(', ') || '특이 flag 없음'}`, severity:(auditBest.quality_grade === 'low' ? 'watch' : 'neutral')},
    {kind:'funnel', title:'후보 선별 병목', body:`최종 ${funnelSummary.final_recommendations ?? 0} · critic high ${funnelSummary.critic_high ?? 0}`, foot:`committee support/watch/reject ${funnelSummary.committee_support ?? 0}/${funnelSummary.committee_watch ?? 0}/${funnelSummary.committee_reject ?? 0}`, severity:(funnelSummary.critic_high ? 'watch' : 'neutral')},
    {kind:'calibration', title:'Calibration', body:`표본 ${calibrationSummary.sample_count ?? recommendationCalibration.sample_count ?? 0}`, foot:(recommendationCalibration.findings || []).slice(0,2).map((f) => `${f.area}: ${f.finding}`).join('<br>') || '현재 발견된 보정 이슈 없음', severity:'neutral'},
    {kind:'target', title:'목표수익률 Arm', body:`rows ${targetReturnEval.rows_scanned ?? '-'} · proposals ${targetReturnEval.proposal_count ?? 0}`, foot:targetArms.map((a) => `${a.adjustment_pct_points}%p complete=${a.complete_count ?? 0} need=${a.needed_complete_count ?? 0}`).join('<br>') || 'arm backlog 없음', severity:'neutral'},
    {kind:'supply', title:'수급가중치 검증', body:`rows ${supplyWeightEval.rows_scanned ?? '-'} · proposals ${supplyWeightEval.proposal_count ?? 0}`, foot:Object.entries(supplyBuckets).slice(0,3).map(([k,v]) => `${k}: n=${v.sample_count ?? 0}, complete=${v.complete_count ?? 0}`).join('<br>') || '평가 요약 없음', severity:'neutral'}
  ], (row) => `<article class="audit-card ${row.severity || 'neutral'}"><div class="audit-card-top"><strong>${row.title}</strong><span class="badge neutral">${row.kind}</span></div><div class="audit-sub">${row.body}</div><div class="audit-foot">${row.foot}</div></article>`);
}


function evalPayloadForFreshness(orgEvaluation) {
  return (orgEvaluation && (orgEvaluation.payload || orgEvaluation)) || {};
}

function flattenWalkForward(research) {
  return research?.walk_forward?.results || [];
}

async function load() {
  const [research, org, orchestrator, pipeline, orgEvaluation, orgGuardian, integrity, marketShock, themeSpillover, scout, curator, universe, recommendations, shadowRecommendations, recommendationAudit, recommendationOutcomes, candidateFunnel, recommendationCalibration, validationSummary, validationWorkerStatus, strategies, coverage, runs, names, disclosures, disclosureFeatures, symbolReviewHistory] = await Promise.all([
    api('/api/research/stock/latest'),
    Promise.resolve({status:'deprecated', source:'pipeline_snapshot'}),
    api('/api/research/org/orchestrator/latest'),
    api('/api/research/pipeline/latest?detail=full'),
    api('/api/research/org/evaluation/latest'),
    api('/api/research/org/improvement-guardian/latest').catch(() => ({status:'not_run'})),
    api('/api/research/integrity/latest').catch(() => ({status:'not_run', summary:{}})),
    api('/api/research/market-shock/latest').catch(() => ({status:'not_run', summary:{}})),
    api('/api/research/theme-spillover/latest').catch(() => ({status:'not_run', summary:{}})),
    api('/api/research/scout/latest'),
    api('/api/research/curator/latest'),
    api('/api/research/universe?limit=1000'),
    api('/api/recommendations/latest?detail=full'),
    apiWithStaticFallback('/api/recommendations/shadow/latest','/static/shadow_recommendations_latest.json',{items: []}),
    api('/api/recommendations/audit/latest?limit=80&dedupe=true'),
    apiWithStaticFallback('/api/recommendations/daily-outcomes?limit=1500','/static/recommendation_daily_outcomes.json',{items: [], summary: {}}),
    api('/api/research/recommendation-funnel/latest').catch(() => ({stages: [], summary: {}})),
    api('/api/recommendations/calibration/latest').catch(() => ({summary: {}, findings: []})),
    api('/api/validation/summary'),
    api('/api/validation/worker-status').catch(() => apiWithStaticFallback('/api/validation/worker-status','/static/validation_worker_status_latest.json',{})),
    api('/api/strategies'),
    api('/api/validation/coverage'),
    api('/api/backtests/runs?limit=50'),
    api('/api/symbols/names'),
    api('/api/disclosures?limit=50'),
    api('/api/disclosures/features?symbols=AAPL,MSFT,NVDA,SPY,QQQ,005930.KS,000660.KS,035420.KS'),
    api('/api/research/symbol-review/history?limit=20').catch(() => ({items: []})),
  ]);
  symbolNames = names.names || {};
  // The names endpoint is mostly static and may lag newly-discovered symbols until service restart.
  // Merge fresh names carried by recommendation/curator/scout payloads so the Recommendation menu
  // does not fall back to raw tickers after universe discovery expands the pool.
  [recommendations.items || [], curator.items || [], scout.selected || []].flat().forEach((row) => {
    if (row?.symbol && row?.name && row.name !== row.symbol) symbolNames[row.symbol] = row.name;
  });
  latestRecommendationBySymbol = new Map((recommendations.items || []).map((row) => [String(row.symbol || '').toUpperCase(), row]));
  const rankedRecommendations = sortRecommendations(recommendations.items || []);
  recommendationSymbolRanks = new Map(rankedRecommendations.map((row, idx) => [row.symbol, idx]));
  recommendationMarketRanks = new Map();
  ['KR', 'US'].forEach((market) => {
    recommendationMarketRanks.set(market, new Map(sortRecommendations((recommendations.items || []).filter((row) => marketOf(row.symbol) === market)).map((row, idx) => [row.symbol, idx])));
  });
  const orch = orchestrator.payload || orchestrator;
  const pipe = pipeline.payload || pipeline;
  const integrityPayload = integrity.payload || integrity || {};
  const integritySummary = integrityPayload.summary || {};
  window.__lastPipelineSnapshot = pipe;
  const orgStepRows = pipe.steps || orch.actions || [];
  const orgFailureCount = orgStepRows.filter((s) => {
    if (['failed', 'failed_required', 'error'].includes(s.status)) return true;
    if (s.error === true) return true;
    return typeof s.returncode === 'number' && s.returncode !== 0;
  }).length;
  const orgRoles = orgStepRows.map((action) => ({
    role: action.display_name || action.name || action.agent || (action.cmd || [])[1]?.split('/').pop() || 'unknown',
    status: action.status || (action.error ? 'error' : 'ok'),
    returncode: action.returncode,
    summary: (action.warnings || []).join('; ') || action.role_summary || action.stderr_tail || (action.stdout_tail || '').slice(-300),
  }));
  setHtml('org-metrics', `
    <div class="metric-card verdict ${pipe.status || ''}"><div class="metric-label">Pipeline</div><div class="metric-value">${pipe.status || 'not_run'}</div></div>
    <div class="metric-card"><div class="metric-label">Run At</div><div class="metric-value">${pipe.run_at ? new Date(pipe.run_at).toLocaleString() : (orch.run_at ? new Date(orch.run_at).toLocaleString() : '-')}</div></div>
    <div class="metric-card"><div class="metric-label">Agents</div><div class="metric-value">${(pipe.steps || []).length || (orch.actions || []).length || '-'}</div></div>
    <div class="metric-card"><div class="metric-label">Failures</div><div class="metric-value">${orgFailureCount}</div></div>
    <div class="metric-card"><div class="metric-label">Active</div><div class="metric-value">${pipe.after?.strategy_status?.active ?? orch.after?.status_counts?.active ?? '-'}</div></div>
    <div class="metric-card"><div class="metric-label">Recommendations</div><div class="metric-value">${pipe.recommendations_summary?.item_count ?? '-'}</div></div>
    <div class="metric-card"><div class="metric-label">Integrity</div><div class="metric-value">${integritySummary.problem_count ?? 0}/${integritySummary.warning_count ?? 0}</div><div class="metric-label">problems/warnings</div></div>
    <div class="metric-card"><div class="metric-label">Service Restarts</div><div class="metric-value">${integritySummary.service_n_restarts ?? '-'}</div><div class="metric-label">systemd counter</div></div>
  `);
  const degradedSteps = (pipe.steps || []).filter((s) => s.status === 'degraded');
  const missingRoleSteps = (pipe.steps || []).filter((s) => !s.display_name || !s.role_summary);
  const orgSnapshotRunAt = pipe.run_at || orch.run_at || null;
  const evalRunAt = evalPayloadForFreshness(orgEvaluation)?.run_at || null;
  const guardianRunAtForFreshness = (orgGuardian && orgGuardian.status !== 'not_run' ? orgGuardian.run_at : null) || guardianPayloadFromPipeline(pipe)?.run_at || null;
  const freshnessBits = [
    orgSnapshotRunAt ? `pipeline ${new Date(orgSnapshotRunAt).toLocaleString()}` : null,
    evalRunAt ? `evaluation ${new Date(evalRunAt).toLocaleString()}` : null,
    guardianRunAtForFreshness ? `guardian ${new Date(guardianRunAtForFreshness).toLocaleString()}` : null,
  ].filter(Boolean).join(' · ');
  setHtml('org-status-note', `<span class="status-chip ${degradedSteps.length ? 'warn' : 'good'}">주의 ${degradedSteps.length}개</span><span class="status-chip ${missingRoleSteps.length ? 'warn' : 'good'}">역할요약 누락 ${missingRoleSteps.length}개</span>${freshnessBits ? `<span class="status-chip info">${freshnessBits}</span>` : ''}<details class="status-help"><summary>상태 기준</summary><p>정상은 최신 pipeline 기준 문제 없음, 주의는 실행 완료 후 non-blocking 경고, 복구 필요는 추천 신뢰 전 조치가 필요한 상태입니다.</p></details>`);
  setHtml('recommendation-audit-metrics', `
    <div class="metric-card verdict ${validationSummary.best?.verdict || recommendationAudit.summary?.best?.verdict || recommendationAudit.summary?.verdict || ''}"><div class="metric-label">신뢰 라벨</div><div class="metric-value">${validationSummary.best?.verdict || recommendationAudit.summary?.best?.verdict || recommendationAudit.summary?.verdict || '-'}</div></div>
    <div class="metric-card"><div class="metric-label">국면 적합</div><div class="metric-value small-text">${recommendationAudit.summary?.best?.best_use || recommendationAudit.summary?.best?.role_label || '-'}</div></div>
    <div class="metric-card"><div class="metric-label">주의 라벨</div><div class="metric-value small-text">${(recommendationAudit.summary?.best?.quality_flags || []).slice(0,2).map((f)=>auditFlagLabel(f,false)).join(' · ') || '-'}</div></div>
    <div class="metric-card"><div class="metric-label">권장 사용처</div><div class="metric-value small-text">${recommendationAudit.summary?.best?.fund_usage_hint || recommendationAudit.summary?.best?.best_use || '-'}</div></div>
    <div class="metric-card"><div class="metric-label">회피 맥락</div><div class="metric-value small-text">${(recommendationAudit.summary?.best?.avoid_contexts || recommendationAudit.summary?.best?.unfavorable_contexts || []).slice(0,2).join(' · ') || '-'}</div></div>
  `);
  const outcomeSummary = recommendationOutcomes.summary || {};
  const outcomeRows = recommendationOutcomes.items || [];
  const byAction = outcomeSummary.by_market || outcomeSummary.by_action || [];
  const byConfidence = outcomeSummary.recent_days || outcomeSummary.by_confidence || [];
  const completeRows = outcomeRows.filter((row) => row.status === 'complete' || row.status === 'stopped_out');
  const pendingRows = outcomeRows.filter((row) => row.status === 'pending');
  const bestOutcome = [...byAction].sort((a, b) => (b.avg_excess_pct || -999) - (a.avg_excess_pct || -999))[0];
  const buy20 = null;
  setHtml('recommendation-outcome-summary', `일별 canonical 성과 ${outcomeRows.length}개 · 완료 ${completeRows.length}개 · 대기 ${pendingRows.length}개. 장 시작 추천 1개와 장 마감 검증 1개만 성과에 반영합니다.`);
  const outcomeMetricsHtml = `
    <div class="metric-card"><div class="metric-label">완료</div><div class="metric-value">${completeRows.length}</div></div>
    <div class="metric-card"><div class="metric-label">대기</div><div class="metric-value">${pendingRows.length}</div></div>
    <div class="metric-card"><div class="metric-label">Best Market</div><div class="metric-value">${bestOutcome ? `${bestOutcome.market}` : '-'}</div><div class="metric-label">${bestOutcome ? `초과 ${pct(bestOutcome.avg_excess_pct)} · n=${bestOutcome.n}` : ''}</div></div>
    <div class="metric-card"><div class="metric-label">Daily Hit</div><div class="metric-value">${bestOutcome ? pct(bestOutcome.hit_rate_pct) : '-'}</div><div class="metric-label">open→close canonical</div></div>
    <div class="metric-card"><div class="metric-label">최근 일자</div><div class="metric-value">${byConfidence.length}</div></div>
  `;
  setHtml('recommendation-outcome-metrics', `
    <div class="metric-card"><div class="metric-label">완료</div><div class="metric-value">${completeRows.length}</div></div>
    <div class="metric-card"><div class="metric-label">대기</div><div class="metric-value">${pendingRows.length}</div></div>
    <div class="metric-card"><div class="metric-label">Best Market</div><div class="metric-value">${bestOutcome ? `${bestOutcome.market}` : '-'}</div><div class="metric-label">${bestOutcome ? `초과 ${pct(bestOutcome.avg_excess_pct)} · n=${bestOutcome.n}` : ''}</div></div>
    <div class="metric-card"><div class="metric-label">Daily Hit</div><div class="metric-value">${bestOutcome ? pct(bestOutcome.hit_rate_pct) : '-'}</div><div class="metric-label">open→close canonical</div></div>
    <div class="metric-card"><div class="metric-label">최근 일자</div><div class="metric-value">${byConfidence.length}</div></div>
  `);
  renderTable('recommendation-outcome-table', ['Date', '시장', '종목', '판정', '상태', '시초/마감', '수익', '벤치', '초과', 'Hit'], outcomeRows.slice(0, 80), (row) => `
    <tr>
      <td>${row.trade_date || (row.run_at ? row.run_at.slice(0,10) : '-')}<br><span class="hint">${row.open_run_at ? new Date(row.open_run_at).toLocaleTimeString() : ''}</span></td>
      <td>${row.market || '-'}</td>
      <td>${nameOf(row.symbol)}</td>
      <td>${row.action}</td>
      <td><span class="${badgeClass(row.status)}">${row.status}</span></td>
      <td>${fmt(row.entry_close)} → ${fmt(row.final_close)}<br><span class="hint">${row.final_date || '-'}</span></td>
      <td>${pct(row.forward_return_pct)}</td>
      <td>${row.benchmark_symbol || '-'} ${pct(row.benchmark_return_pct)}</td>
      <td>${pct(row.excess_return_pct)}</td>
      <td>${row.hit === null || row.hit === undefined ? '-' : (row.hit ? 'Y' : 'N')}</td>
    </tr>`);
  const funnelStages = candidateFunnel.stages || [];
  const funnelSummary = candidateFunnel.summary || {};
  const calibrationSummary = recommendationCalibration.summary || {};
  const calibrationFindings = recommendationCalibration.findings || [];
  const stageLabel = {
    discovered: '발굴',
    price_refreshed: '가격갱신',
    data_quality_fail: '품질실패',
    data_quality_watch: '품질관찰',
    curated_active: 'Active',
    scout_selected: 'Scout',
    recommendation_candidates: '추천후보',
    final_recommendations: '최종추천',
    critic_high: 'Critic High',
    committee_support: '위원회 Support',
    committee_watch: '위원회 Watch',
    committee_reject: '위원회 Reject'
  };
  setHtml('candidate-funnel-summary', `최종 추천 ${funnelSummary.final_recommendations ?? 0}건 · critic high ${funnelSummary.critic_high ?? 0}건 · committee support/watch/reject ${funnelSummary.committee_support ?? 0}/${funnelSummary.committee_watch ?? 0}/${funnelSummary.committee_reject ?? 0}건 · calibration 표본 ${calibrationSummary.sample_count ?? recommendationCalibration.sample_count ?? 0}건.`);
  setHtml('candidate-funnel-metrics', funnelStages.slice(0, 8).map((stage) => `
    <div class="metric-card"><div class="metric-label">${stageLabel[stage.stage] || stage.stage}</div><div class="metric-value">${stage.count ?? 0}</div><div class="metric-label">${stage.by_action ? Object.entries(stage.by_action).map(([k,v])=>`${k}:${v}`).join(' · ') : ''}</div></div>
  `).join(''));
  renderCards('calibration-findings', calibrationFindings.length ? calibrationFindings : [{severity:'neutral', area:'calibration', finding:'완료 outcome 표본이 부족하거나 현재 발견된 보정 이슈가 없습니다.', recommendation:'1D/5D/20D outcome이 쌓이면 점수/critic/committee bucket별 품질을 계속 비교합니다.'}], (row) => `
    <article class="audit-card ${row.severity || 'neutral'}"><div class="audit-card-top"><strong>${row.area || 'calibration'}</strong><span class="badge neutral">${row.severity || 'info'}</span></div>
    <div class="audit-sub">${row.finding || '-'}</div>
    <div class="audit-foot">${row.recommendation || ''}</div></article>`, 3);
  let auditedCandidates = recommendationAudit.items || [];
  if (auditResultFilter !== 'all') auditedCandidates = auditedCandidates.filter((row) => row.result === auditResultFilter);
  if (auditSymbolFilter) auditedCandidates = auditedCandidates.filter((row) => String(row.symbol || '').toUpperCase().includes(auditSymbolFilter.toUpperCase()));
  const latestAuditCutoff = recommendationAudit.latest_cutoff || auditedCandidates[0]?.cutoff || '-';
  const auditRunAt = recommendationAudit.run_at ? new Date(recommendationAudit.run_at).toLocaleString() : '-';
  setHtml('audit-summary-text', `<b>${validationSummary.best?.logic || recommendationAudit.summary?.best_logic || '-'}</b> · ${recommendationAudit.summary?.best?.best_use || recommendationAudit.summary?.best?.role_label || validationSummary.best?.verdict || recommendationAudit.summary?.best?.verdict || '조건 라벨 검토'} · ${(recommendationAudit.summary?.best?.quality_flags || validationSummary.best?.quality_flags || []).slice(0,3).map((f)=>auditFlagLabel(f,false)).join(', ') || '특이 flag 낮음'} · 최신 컷오프 ${latestAuditCutoff} · 갱신 ${auditRunAt}`);
  renderTable('recommendation-audit-table', ['종목', '결과', '전략', 'Cutoff', '최종', '초과', '최대상승', '최대하락', '해석'], auditedCandidates, (row) => `
    <tr><td>${nameOf(row.symbol)}</td><td><span class="${badgeClass(row.result)}">${row.result}</span></td><td><code>${row.logic}</code></td><td>${row.cutoff}</td><td>${pct(row.final_return_pct)}</td><td>${pct(row.excess_return_pct)}</td><td>${pct(row.max_upside_pct)}</td><td>${pct(row.max_drawdown_pct)}</td><td class="reason-cell">${auditOutcomeText(row)} ${row.attribution ? `<br><span class="hint">${row.attribution}</span>` : ''}</td></tr>`);
  renderCards('recommendation-audit-cards', auditedCandidates, (row) => `
    <article class="audit-card ${row.result}"><div class="audit-card-top"><strong>${nameOf(row.symbol)}</strong><span class="${badgeClass(row.result)}">${row.result}</span></div>
    <div class="audit-sub">${row.logic} · ${row.cutoff} 기준 · ${row.horizon_days} 거래일 검증</div>
    <div class="audit-levels"><div><span>진입</span><b>${fmt(row.entry)}</b></div><div><span>목표</span><b>${fmt(row.target)}</b></div><div><span>손절</span><b>${fmt(row.stop)}</b></div></div>
    <p class="audit-explain">${auditOutcomeText(row)} ${row.attribution ? `<br><b>원인 추정</b>: ${row.attribution}` : ''}</p>
    <div class="audit-foot">${row.days_to_event}일 · 최종 ${pct(row.final_return_pct)} · 초과 ${pct(row.excess_return_pct)} · 최대상승 ${pct(row.max_upside_pct)} · 최대하락 ${pct(row.max_drawdown_pct)}</div></article>`);
  setHtml('audit-summary-text', `${orchestrator.summary || ''}<br>` + (document.getElementById('audit-summary-text')?.innerHTML || ''));

  const underTested = coverage.under_tested || [];
  const underTestedHtml = underTested.slice(0, 5).map((x) => `
    <div class="metric-card"><div class="metric-label">저샘플 전략</div><div class="metric-value small-text">${x.logic || '-'}</div><div class="metric-label">samples ${x.candidate_samples ?? 0} · done ${x.completed ?? 0}</div></div>`).join('');
  setHtml('coverage-metrics', `
    <div class="metric-card"><div class="metric-label">전략 후보</div><div class="metric-value">${coverage.strategy_count ?? '-'}</div></div>
    <div class="metric-card"><div class="metric-label">검증 결과</div><div class="metric-value">${coverage.completed_results ?? '-'}</div></div>
    <div class="metric-card"><div class="metric-label">커버리지</div><div class="metric-value">${pct(coverage.coverage_pct_estimate)}</div></div>
    <div class="metric-card"><div class="metric-label">대기</div><div class="metric-value">${coverage.by_status?.pending_validation ?? 0}</div></div>
    <div class="metric-card"><div class="metric-label">Active</div><div class="metric-value">${coverage.by_status?.active ?? 0}</div></div>
    ${underTestedHtml}`);

  renderValidationMain(pipe, recommendations, recommendationOutcomes, recommendationAudit, candidateFunnel, recommendationCalibration, validationSummary, validationWorkerStatus);
  renderFundPerformancePanel(pipe, recommendations);
  renderFundLeaderboard();
  renderFundTradeHistory(document.getElementById('fund-trade-filter')?.value || '');

  const evalPayload = orgEvaluation.payload || orgEvaluation;
  const orgFindings = evalPayload.findings || [];
  const orgActionCount = orgFindings.filter((x) => ['urgent','action'].includes(x.severity)).length;
  const orgWatchCount = orgFindings.filter((x) => x.severity === 'watch').length;
  const guardianPayload = (orgGuardian && orgGuardian.status !== 'not_run' ? orgGuardian : null) || guardianPayloadFromPipeline(pipe) || {};
  const guardianSummary = guardianPayload.summary || {};
  const guardianRunAt = guardianPayload.run_at ? new Date(guardianPayload.run_at).toLocaleString() : '-';
  setHtml('org-evaluation-metrics', `
    <div class="metric-card verdict ${evalPayload.verdict || ''}"><div class="metric-label">판정</div><div class="metric-value">${evalPayload.verdict || '-'}</div></div>
    <div class="metric-card"><div class="metric-label">건강점수</div><div class="metric-value">${evalPayload.health_score ?? '-'}</div></div>
    <div class="metric-card"><div class="metric-label">구조 조치</div><div class="metric-value">${orgActionCount}</div><div class="metric-label">긴급/조치 findings</div></div>
    <div class="metric-card"><div class="metric-label">관찰</div><div class="metric-value">${orgWatchCount}</div><div class="metric-label">관찰 findings</div></div>
    <div class="metric-card"><div class="metric-label">자동처리</div><div class="metric-value">${guardianSummary.auto_applied_count ?? '-'}</div><div class="metric-label">가디언 저위험</div></div>
    <div class="metric-card"><div class="metric-label">제안 대기</div><div class="metric-value">${guardianSummary.patch_proposal_count ?? '-'}</div><div class="metric-label">패치 제안</div></div>
    <div class="metric-card"><div class="metric-label">승인 필요</div><div class="metric-value">${guardianSummary.approval_required_count ?? '-'}</div><div class="metric-label">수동 승인</div></div>
    <div class="metric-card"><div class="metric-label">평가 갱신</div><div class="metric-value">${evalPayload.run_at ? new Date(evalPayload.run_at).toLocaleString() : '-'}</div><div class="metric-label">guardian ${guardianRunAt}</div></div>`);
  renderFundOrgSummary(pipe);
  renderFundOrgStaticFallback(pipe);
  renderSupervisorDomainCards(pipe, evalPayload, guardianPayload);
  renderCards('org-evaluation-cards', evalPayload.findings || [], (row) => {
    const label = row.severity === 'action' || row.severity === 'urgent' ? '조치 권고' : (row.severity === 'watch' ? '관찰 지침' : '상태 메모');
    const sevLabel = orgSeverityLabel(row.severity);
    return `<article class="audit-card ${row.severity}"><div class="audit-card-top"><strong>${row.area}</strong><span class="badge ${orgSeverityClass(row.severity)}">${sevLabel}</span></div>
    <div class="audit-sub">${row.finding}</div>
    <div class="audit-foot"><b>${label}</b>: ${row.recommendation}</div></article>`;
  });

  const guardianRows = [
    ...(guardianPayload.auto_applied || []).map((row) => ({...row, kind:'자동 반영'})),
    ...(guardianPayload.patch_proposals || []).map((row) => ({...row, kind:'패치 제안'})),
    ...(guardianPayload.approval_required || []).map((row) => ({...row, kind:'승인 필요'})),
    ...(guardianPayload.classified_findings || []).filter((row) => row.class === 'observe').slice(0, 4).map((row) => ({...row, kind:'관찰'})),
  ];
  const lifecycleGates = guardianPayload.lifecycle_bottlenecks || [];
  if (lifecycleGates.length) {
    setHtml('org-evaluation-cards', (document.getElementById('org-evaluation-cards')?.innerHTML || '') + `
      <article class="audit-card watch lifecycle-bottleneck-summary"><div class="audit-card-top"><strong>품질 게이트 상태</strong><span class="badge warn">검증 누적 중</span></div>
      <div class="audit-sub">장애가 아니라 paper-buy/active 승격을 막는 품질 기준입니다. Booster가 추천+fund consensus 후보의 historical sample을 계속 누적합니다.</div>
      <div class="audit-foot">${lifecycleGates.slice(0,5).map((g) => `<b>${lifecycleGateLabel(g.gate)}</b>: ${g.detail || ''}`).join('<br>')}</div></article>`);
  }
  if (guardianRows.length) {
    setHtml('org-evaluation-cards', (document.getElementById('org-evaluation-cards')?.innerHTML || '') + `
      <article class="audit-card neutral org-guardian-summary"><div class="audit-card-top"><strong>개선 반영 현황</strong><span class="badge good">Guardian</span></div>
      <div class="audit-sub">자동처리 ${guardianSummary.auto_applied_count ?? 0} · 패치제안 ${guardianSummary.patch_proposal_count ?? 0} · 승인필요 ${guardianSummary.approval_required_count ?? 0} · 관찰 ${guardianSummary.observe_count ?? 0}</div>
      <div class="audit-foot">${guardianRows.map((row) => { const gates = row.blocking_gates || row.evidence?.blocking_gates || []; const gateText = gates.length ? ` · blockers: ${gates.slice(0,4).map((g) => g.gate || g.detail || '').filter(Boolean).join(', ')}` : ''; return `<b>${row.kind}</b>: ${row.action || row.title || row.area || '-'}${row.symbols ? ` (${row.symbols.join(', ')})` : ''}${row.reason ? ` · ${row.reason}` : ''}${gateText}`; }).join('<br>')}</div></article>`);
  }
  const alphaAgenda = orch.alpha_agenda || [];
  const assignedTasks = orch.assigned_research_tasks || [];
  const activeGap = orch.active_pool_gap || {};
  if (alphaAgenda.length || assignedTasks.length) {
    setHtml('org-evaluation-cards', (document.getElementById('org-evaluation-cards')?.innerHTML || '') + `
      <article class="audit-card action alpha-orchestrator-summary"><div class="audit-card-top"><strong>수익률 개선 오케스트레이터</strong><span class="badge bad">Alpha Agenda</span></div>
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

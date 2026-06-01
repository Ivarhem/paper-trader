const ROOT_PATH = (document.querySelector('meta[name="root-path"]')?.content || '').replace(/\/$/, '');
function redirectToLogin() {
  const current = `${location.pathname.replace(ROOT_PATH || '', '')}${location.search || ''}` || '/dashboard';
  location.href = `${ROOT_PATH}/login?next=${encodeURIComponent(current)}`;
}

const state = {
  watchlist: [],
  signals: [],
  portfolio: null,
  trades: [],
  selectedSymbol: null,
  strategy: {
    short_window: 5,
    long_window: 20,
  },
};

async function api(path, options = {}) {
  const url = path.startsWith("http") ? path : `${ROOT_PATH}${path}`;
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (response.status === 401) {
    redirectToLogin();
    throw new Error('로그인이 만료되었습니다. 다시 로그인하세요.');
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Request failed");
  }
  return response.json();
}

function formatMoney(value) {
  return new Intl.NumberFormat("ko-KR", {
    style: "currency",
    currency: "KRW",
    maximumFractionDigits: 0,
  }).format(value || 0);
}

function formatPrice(value, symbol = "") {
  const upper = String(symbol || "").toUpperCase();
  const currency = upper.startsWith("KRW-") || upper.endsWith(".KS") || upper.endsWith(".KQ") ? "KRW" : "USD";
  return new Intl.NumberFormat(currency === "KRW" ? "ko-KR" : "en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: currency === "KRW" ? 0 : 2,
  }).format(value || 0);
}

function formatNumber(value) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 }).format(value || 0);
}

function showMessage(text) {
  const el = document.createElement("div");
  el.className = "message";
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2400);
}

function renderTable(targetId, columns, rows, rowRenderer) {
  const target = document.getElementById(targetId);
  if (!rows.length) {
    target.innerHTML = document.getElementById("empty-state").innerHTML;
    return;
  }
  const head = columns.map((column) => `<th>${column}</th>`).join("");
  const body = rows.map(rowRenderer).join("");
  target.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderPortfolio() {
  const target = document.getElementById("portfolio-metrics");
  if (!state.portfolio) return;
  const fx = state.portfolio.fx || {};
  const metrics = [
    ["Cash", formatMoney(state.portfolio.cash)],
    ["Positions", formatMoney(state.portfolio.positions_value)],
    ["Total Value", formatMoney(state.portfolio.total_value)],
    ["Unrealized PnL", formatMoney(state.portfolio.unrealized_pnl)],
    ["Realized PnL", formatMoney(state.portfolio.realized_pnl)],
    ["USD/KRW", fx.rate ? `${formatNumber(fx.rate)} (${fx.date || "n/a"})` : "n/a"],
  ];
  target.innerHTML = metrics
    .map(
      ([label, value]) => `
        <div class="metric-card">
          <div class="metric-label">${label}</div>
          <div class="metric-value">${value}</div>
        </div>
      `
    )
    .join("");

  renderTable(
    "positions-table",
    ["Symbol", "Qty", "Avg Cost (KRW)", "Last", "Market Value", "Unrealized"],
    state.portfolio.positions,
    (row) => `
      <tr>
        <td>${row.symbol}</td>
        <td>${formatNumber(row.quantity)}</td>
        <td>${formatMoney(row.average_cost)}</td>
        <td>${formatPrice(row.last_price, row.symbol)}${row.last_price_krw && row.last_price_krw !== row.last_price ? `<br><span class="muted">${formatMoney(row.last_price_krw)} @ ${formatNumber(row.fx_rate)}</span>` : ""}</td>
        <td>${formatMoney(row.market_value)}</td>
        <td>${formatMoney(row.unrealized_pnl)}</td>
      </tr>
    `
  );
}

function renderWatchlist() {
  renderTable(
    "watchlist-table",
    ["Symbol", "Note", ""],
    state.watchlist,
    (row) => `
      <tr>
        <td><button class="linkish" data-symbol="${row.symbol}">${row.symbol}</button></td>
        <td>${row.note || ""}</td>
        <td><button class="danger delete-watchlist" data-id="${row.id}">Delete</button></td>
      </tr>
    `
  );

  const symbols = state.watchlist.map((item) => item.symbol);
  const options = symbols.map((symbol) => `<option value="${symbol}">${symbol}</option>`).join("");
  document.getElementById("symbol-select").innerHTML = options;
  document.getElementById("trade-symbol").innerHTML = options;
  if (!state.selectedSymbol && symbols.length) {
    state.selectedSymbol = symbols[0];
  }
  if (state.selectedSymbol) {
    document.getElementById("symbol-select").value = state.selectedSymbol;
    document.getElementById("trade-symbol").value = state.selectedSymbol;
  }
}

function renderSignals() {
  renderTable(
    "signals-table",
    ["Symbol", "Market", "TF", `MA(${state.strategy.short_window})`, `MA(${state.strategy.long_window})`, "Close", "RSI(14)", "Vol", "High Dist", "Signal"],
    state.signals,
    (row) => `
      <tr>
        <td>${row.symbol}</td>
        <td>${row.market || "stock"}</td>
        <td>${row.timeframe || "1d"}</td>
        <td>${row.ma_short ? formatPrice(row.ma_short, row.symbol) : "-"}</td>
        <td>${row.ma_long ? formatPrice(row.ma_long, row.symbol) : "-"}</td>
        <td>${formatPrice(row.latest_close, row.symbol)}</td>
        <td>${row.rsi_14 !== null ? row.rsi_14.toFixed(2) : "-"}</td>
        <td>${row.volatility_20d_pct !== null ? `${row.volatility_20d_pct.toFixed(2)}%` : "-"}</td>
        <td>${row.distance_52w_high_pct !== null ? `${row.distance_52w_high_pct.toFixed(2)}%` : "-"}</td>
        <td><span class="tag ${row.crossover_signal.includes("bearish") ? "bearish" : ""}">${row.crossover_signal}</span></td>
      </tr>
    `
  );
}

function renderTrades() {
  renderTable(
    "trades-table",
    ["Time", "Symbol", "Side", "Qty", "Price", "Notional"],
    state.trades,
    (row) => `
      <tr>
        <td>${new Date(row.executed_at).toLocaleString()}</td>
        <td>${row.symbol}</td>
        <td>${row.side}</td>
        <td>${formatNumber(row.quantity)}</td>
        <td>${formatMoney(row.price)}</td>
        <td>${formatMoney(row.notional)}</td>
      </tr>
    `
  );
}

async function renderSymbolDetail() {
  const target = document.getElementById("symbol-detail");
  if (!state.selectedSymbol) {
    target.innerHTML = document.getElementById("empty-state").innerHTML;
    return;
  }
  const [signal, prices] = await Promise.all([
    api(`/api/signals/${state.selectedSymbol}?short_window=${state.strategy.short_window}&long_window=${state.strategy.long_window}`),
    api(`/api/prices/${state.selectedSymbol}`),
  ]);
  const recent = prices.prices.slice(-5).reverse();
  target.innerHTML = `
    <div class="metric-card">
      <div class="metric-label">Latest Close</div>
      <div class="metric-value">${formatPrice(signal.latest_close, signal.symbol)}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Latest Date</div>
      <div class="metric-value">${signal.latest_date}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Crossover</div>
      <div class="metric-value">${signal.crossover_signal}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">5D Momentum</div>
      <div class="metric-value">${signal.momentum_5d_pct !== null ? `${signal.momentum_5d_pct}%` : "-"}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">RSI (14)</div>
      <div class="metric-value">${signal.rsi_14 !== null ? signal.rsi_14 : "-"}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Volatility (20D)</div>
      <div class="metric-value">${signal.volatility_20d_pct !== null ? `${signal.volatility_20d_pct}%` : "-"}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">52W High Distance</div>
      <div class="metric-value">${signal.distance_52w_high_pct !== null ? `${signal.distance_52w_high_pct}%` : "-"}</div>
    </div>
    <div style="grid-column:1/-1;">
      <table>
        <thead><tr><th>Date</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th></tr></thead>
        <tbody>
          ${recent
            .map(
              (row) => `
                <tr>
                  <td>${row.date}</td>
                  <td>${formatPrice(row.open, row.symbol)}</td>
                  <td>${formatPrice(row.high, row.symbol)}</td>
                  <td>${formatPrice(row.low, row.symbol)}</td>
                  <td>${formatPrice(row.close, row.symbol)}</td>
                  <td>${formatNumber(row.volume)}</td>
                </tr>
              `
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

async function load() {
  const [watchlist, signals, portfolio, trades] = await Promise.all([
    api("/api/watchlist"),
    api(`/api/signals?short_window=${state.strategy.short_window}&long_window=${state.strategy.long_window}`),
    api("/api/portfolio"),
    api("/api/trades"),
  ]);
  state.watchlist = watchlist.items;
  state.signals = signals.items;
  state.portfolio = portfolio;
  state.trades = trades.items;
  renderWatchlist();
  renderSignals();
  renderPortfolio();
  renderTrades();
  await renderSymbolDetail();
}

document.getElementById("watchlist-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/watchlist", {
      method: "POST",
      body: JSON.stringify({
        symbol: form.get("symbol"),
        note: form.get("note"),
      }),
    });
    event.target.reset();
    await load();
    showMessage("Watchlist updated");
  } catch (error) {
    showMessage(error.message);
  }
});

document.getElementById("watchlist-table").addEventListener("click", async (event) => {
  const deleteButton = event.target.closest(".delete-watchlist");
  const symbolButton = event.target.closest("[data-symbol]");
  if (deleteButton) {
    try {
      await api(`/api/watchlist/${deleteButton.dataset.id}`, { method: "DELETE" });
      await load();
      showMessage("Removed from watchlist");
    } catch (error) {
      showMessage(error.message);
    }
  }
  if (symbolButton) {
    state.selectedSymbol = symbolButton.dataset.symbol;
    document.getElementById("symbol-select").value = state.selectedSymbol;
    document.getElementById("trade-symbol").value = state.selectedSymbol;
    const backtestSymbol = document.getElementById("backtest-symbol");
    if (backtestSymbol) backtestSymbol.value = state.selectedSymbol;
    await renderSymbolDetail();
  }
});

document.getElementById("symbol-select").addEventListener("change", async (event) => {
  state.selectedSymbol = event.target.value;
  document.getElementById("trade-symbol").value = state.selectedSymbol;
  await renderSymbolDetail();
});

document.getElementById("trade-form").addEventListener("click", async (event) => {
  const action = event.target.dataset.side;
  if (!action) return;
  const form = new FormData(document.getElementById("trade-form"));
  try {
    await api(`/api/trades/${action}`, {
      method: "POST",
      body: JSON.stringify({
        symbol: form.get("symbol"),
        quantity: Number(form.get("quantity")),
        price: form.get("price") ? Number(form.get("price")) : null,
      }),
    });
    document.getElementById("trade-form").reset();
    await load();
    showMessage(`Trade ${action} executed`);
  } catch (error) {
    showMessage(error.message);
  }
});

document.getElementById("import-sample-btn").addEventListener("click", async () => {
  try {
    await api("/api/prices/import", {
      method: "POST",
      body: JSON.stringify({ csv_path: "sample_data/prices_sample.csv" }),
    });
    await load();
    showMessage("Sample prices imported");
  } catch (error) {
    showMessage(error.message);
  }
});

document.getElementById("reset-btn").addEventListener("click", async () => {
  try {
    await api("/api/portfolio/reset", { method: "POST" });
    await load();
    showMessage("Portfolio reset");
  } catch (error) {
    showMessage(error.message);
  }
});

load().catch((error) => showMessage(error.message));


document.getElementById("strategy-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const shortWindow = Number(form.get("short_window"));
  const longWindow = Number(form.get("long_window"));
  if (shortWindow >= longWindow) {
    showMessage("Short window must be less than long window");
    return;
  }
  state.strategy.short_window = shortWindow;
  state.strategy.long_window = longWindow;
  await load();
  await renderSymbolDetail();
  showMessage("Strategy updated");
});


document.getElementById("crypto-import-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const result = await api("/api/crypto/upbit/import", {
      method: "POST",
      body: JSON.stringify({
        symbol: form.get("symbol"),
        timeframe: form.get("timeframe"),
        count: Number(form.get("count")),
      }),
    });
    await load();
    state.selectedSymbol = result.symbol;
    await renderSymbolDetail();
    showMessage(`Imported ${result.symbol}: ${result.inserted} new candles`);
  } catch (error) {
    showMessage(error.message);
  }
});


function renderBacktestResult(result) {
  const target = document.getElementById("backtest-result");
  target.innerHTML = `
    <div class="metric-card"><div class="metric-label">Strategy</div><div class="metric-value">${result.strategy}</div></div>
    <div class="metric-card"><div class="metric-label">Return</div><div class="metric-value">${result.total_return_pct}%</div></div>
    <div class="metric-card"><div class="metric-label">Buy & Hold</div><div class="metric-value">${result.buy_hold_return_pct}%</div></div>
    <div class="metric-card"><div class="metric-label">Max DD</div><div class="metric-value">${result.max_drawdown_pct}%</div></div>
    <div class="metric-card"><div class="metric-label">Trades</div><div class="metric-value">${result.trade_count}</div></div>
    <div class="metric-card"><div class="metric-label">Win Rate</div><div class="metric-value">${result.win_rate_pct}%</div></div>
    <div class="metric-card"><div class="metric-label">Profit Factor</div><div class="metric-value">${result.profit_factor ?? "-"}</div></div>
    <div class="metric-card"><div class="metric-label">Final Equity</div><div class="metric-value">${formatMoney(result.final_equity)}</div></div>
  `;
  renderTable(
    "backtest-trades-table",
    ["Date", "Side", "Price", "Qty", "PnL", "Reason"],
    result.trades || [],
    (row) => `
      <tr>
        <td>${row.date}</td>
        <td>${row.side}</td>
        <td>${formatPrice(row.price, result.symbol)}</td>
        <td>${formatNumber(row.quantity)}</td>
        <td>${row.pnl !== undefined ? formatMoney(row.pnl) : "-"}</td>
        <td>${row.reason || "-"}</td>
      </tr>
    `
  );
}

document.getElementById("backtest-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const result = await api("/api/backtests/run", {
      method: "POST",
      body: JSON.stringify({
        symbol: form.get("symbol"),
        strategy: form.get("strategy"),
        initial_cash: Number(form.get("initial_cash")),
        fee_bps: Number(form.get("fee_bps")),
        slippage_bps: Number(form.get("slippage_bps")),
        short_window: state.strategy.short_window,
        long_window: state.strategy.long_window,
      }),
    });
    renderBacktestResult(result);
    showMessage(`Backtest complete: ${result.total_return_pct}%`);
  } catch (error) {
    showMessage(error.message);
  }
});

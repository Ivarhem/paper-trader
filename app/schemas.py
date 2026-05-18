from __future__ import annotations

from pydantic import BaseModel, Field


class WatchlistCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    note: str | None = Field(default=None, max_length=255)


class PriceImportRequest(BaseModel):
    csv_path: str


class TradeRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    quantity: float = Field(..., gt=0)
    price: float | None = Field(default=None, gt=0)


class UpbitImportRequest(BaseModel):
    symbol: str = Field(default="KRW-BTC", min_length=3, max_length=20)
    timeframe: str = Field(default="1h", max_length=10)
    count: int = Field(default=200, ge=1, le=200)


class BacktestRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    strategy: str = Field(default="ma_cross", pattern="^(ma_cross|rsi_reversion)$")
    initial_cash: float = Field(default=100000.0, gt=0)
    fee_bps: float = Field(default=5.0, ge=0)
    slippage_bps: float = Field(default=5.0, ge=0)
    short_window: int = Field(default=5, ge=2, le=100)
    long_window: int = Field(default=20, ge=3, le=300)
    rsi_window: int = Field(default=14, ge=2, le=100)
    rsi_buy: float = Field(default=30.0, ge=1, le=99)
    rsi_sell: float = Field(default=55.0, ge=1, le=99)


class BacktestSweepRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["KRW-BTC"])
    strategies: list[str] = Field(default_factory=lambda: ["ma_cross", "rsi_reversion"])
    initial_cash: float = Field(default=100000.0, gt=0)
    fee_bps: float = Field(default=5.0, ge=0)
    slippage_bps: float = Field(default=5.0, ge=0)
    short_windows: list[int] = Field(default_factory=lambda: [5, 10, 20])
    long_windows: list[int] = Field(default_factory=lambda: [20, 50])
    rsi_buys: list[float] = Field(default_factory=lambda: [25, 30, 35])
    rsi_sells: list[float] = Field(default_factory=lambda: [50, 55, 60])
    min_trades: int = Field(default=2, ge=0)
    limit: int = Field(default=20, ge=1, le=200)


class ExternalContextSnapshotRequest(BaseModel):
    captured_at: str | None = None
    risk_level: str = Field(..., pattern="^(normal|elevated|high|unknown)$")
    market_regime: str = Field(..., min_length=1, max_length=50)
    event_window: str | None = None
    strategy_adjustments: dict = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)


class ForwardSignalRequest(BaseModel):
    signal_at: str | None = None
    symbol: str = Field(..., min_length=1, max_length=20)
    timeframe: str = Field(default="15m", max_length=10)
    strategy: str = Field(..., min_length=1, max_length=50)
    action: str = Field(..., pattern="^(BUY|SELL|HOLD|BLOCKED)$")
    price: float | None = None
    reason: str | None = None
    context_snapshot_id: int | None = None
    payload: dict = Field(default_factory=dict)


class UniverseMemberUpsertRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    status: str = Field(default="watch", pattern="^(active|watch|quarantine|retired)$")
    reason: str | None = Field(default=None, max_length=500)
    score: float | None = None
    payload: dict = Field(default_factory=dict)

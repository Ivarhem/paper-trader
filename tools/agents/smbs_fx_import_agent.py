#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.database import get_connection, init_db
from tools.agents.lib.agent_contract import attach_contract

SMBS_XML_URL = "http://www.smbs.biz/ExRate/StdExRate_xml.jsp"
SMBS_PAGE_URL = "http://www.smbs.biz/ExRate/StdExRate.jsp"
SYMBOL = "USD/KRW"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def num(value) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_date_label(label: str) -> str:
    # SMBS chart labels are yy.mm.dd.
    parts = str(label).strip().split(".")
    if len(parts) != 3:
        raise ValueError(f"unexpected SMBS date label: {label!r}")
    yy, mm, dd = [int(x) for x in parts]
    year = 2000 + yy if yy < 80 else 1900 + yy
    return date(year, mm, dd).isoformat()


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "paper_trader_fx_research/1.0"})
    raw = urllib.request.urlopen(req, timeout=20).read()
    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("euc-kr", errors="replace")


def fetch_xml_rates(start: str, end: str, currency: str = "USD") -> list[dict]:
    arr_value = f"{currency}_{start}_{end}"
    url = SMBS_XML_URL + "?" + urllib.parse.urlencode({"arr_value": arr_value})
    text = fetch_text(url)
    out = []
    for label, value in re.findall(r'<set[^>]*label=["\']([^"\']+)["\'][^>]*value=["\']([^"\']+)["\']', text):
        rate = num(value)
        if rate is None:
            continue
        out.append({
            "date": parse_date_label(label),
            "rate": rate,
            "source_url": url,
            "source": "smbs_std_xml",
        })
    return out


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.in_table = False
        self.in_cell = False
        self.rows = []
        self.row = []
        self.cell = ""

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.in_table = True
            self.rows = []
        elif self.in_table and tag == "tr":
            self.row = []
        elif self.in_table and tag == "td":
            self.in_cell = True
            self.cell = ""

    def handle_data(self, data):
        if self.in_cell:
            self.cell += data

    def handle_endtag(self, tag):
        if self.in_table and tag == "td":
            self.in_cell = False
            self.row.append(" ".join(self.cell.split()))
        elif self.in_table and tag == "tr":
            if self.row:
                self.rows.append(self.row)
        elif tag == "table" and self.in_table:
            self.tables.append(self.rows)
            self.in_table = False


def parse_html_table(path: Path) -> list[dict]:
    parser = TableParser()
    parser.feed(path.read_text(encoding="euc-kr", errors="replace"))
    daily = []
    for table in parser.tables:
        if not table or not table[0] or "날짜" not in table[0][0]:
            continue
        for row in table[1:]:
            if len(row) < 3:
                continue
            rate = num(row[2])
            if not rate:
                continue
            daily.append({
                "date": row[0].replace(".", "-"),
                "rate": rate,
                "currency_name": row[1],
                "change": num(row[3]) if len(row) > 3 else None,
                "open": num(row[4]) if len(row) > 4 else None,
                "high": num(row[5]) if len(row) > 5 else None,
                "low": num(row[6]) if len(row) > 6 else None,
                "close_1530": num(row[7]) if len(row) > 7 else None,
                "close_0200": num(row[8]) if len(row) > 8 else None,
                "source": "smbs_std_html_xls",
            })
    return daily


def upsert_rates(rows: list[dict]) -> dict:
    created = utc_now()
    inserted = updated = skipped = 0
    with get_connection() as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        for row in rows:
            rate = row.get("rate")
            if not rate:
                skipped += 1
                continue
            o = row.get("open") or rate
            h = row.get("high") or rate
            l = row.get("low") or rate
            payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
            cur = conn.execute(
                """
                INSERT INTO price_bars (symbol,date,open,high,low,close,volume,market,exchange,timeframe,created_at)
                VALUES (?,?,?,?,?,?,?,'fx','smbs_std','1d',?)
                ON CONFLICT(symbol,date) DO UPDATE SET
                  open=excluded.open,
                  high=excluded.high,
                  low=excluded.low,
                  close=excluded.close,
                  volume=excluded.volume,
                  market=excluded.market,
                  exchange=excluded.exchange,
                  timeframe=excluded.timeframe
                """,
                (SYMBOL, row["date"], o, h, l, rate, 0.0, created),
            )
            # sqlite rowcount is 1 for insert/update under this upsert form.
            if cur.rowcount:
                inserted += 1
        conn.execute(
            """
            INSERT INTO watchlist_items (symbol,note,created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET note=excluded.note
            """,
            (SYMBOL, "SMBS official daily USD/KRW standard rate", created),
        )
    return {"upserted": inserted + updated, "skipped": skipped}


def default_start(days: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()


def main() -> None:
    ap = argparse.ArgumentParser(description="Import SMBS official daily USD/KRW standard rates")
    ap.add_argument("--start", default=default_start(45))
    ap.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--currency", default="USD")
    ap.add_argument("--input-xls", help="Optional SMBS exported .xls HTML file to import instead of fetching XML.")
    ap.add_argument("--output", default="/tmp/smbs_fx_import_latest.json")
    args = ap.parse_args()

    init_db()
    warnings = []
    if args.input_xls:
        rows = parse_html_table(Path(args.input_xls))
        source = "smbs_export_xls"
    else:
        rows = fetch_xml_rates(args.start, args.end, args.currency)
        source = "smbs_xml"

    rows = sorted({r["date"]: r for r in rows}.values(), key=lambda r: r["date"])
    result = upsert_rates(rows)
    if not rows:
        warnings.append("no SMBS FX rates parsed")

    latest = rows[-1] if rows else None
    packet = {
        "run_at": utc_now(),
        "mode": "smbs_official_fx_import",
        "provider": "서울외국환중개",
        "authority": "official_daily_standard_rate_reference_only_no_trading",
        "symbol": SYMBOL,
        "source": source,
        "start": args.start,
        "end": args.end,
        "row_count": len(rows),
        "latest": latest,
        "db_result": result,
        "warnings": warnings,
        "next_actions": ["Use USD/KRW:smbs_std as accounting FX; fallback to KRW=X only when official rate is unavailable."],
    }
    attach_contract(packet, "smbs_fx_import_agent", status="degraded" if warnings else "ok", outputs={"row_count": len(rows), "latest": latest}, metrics={"row_count": len(rows)}, warnings=warnings, next_actions=packet["next_actions"])
    Path(args.output).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "static/smbs_fx_import_latest.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(packet, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

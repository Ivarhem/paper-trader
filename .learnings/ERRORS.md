# Errors

Command failures and integration errors.

---

## 2026-05-11 - paper_trader seed candidate check path/quoting errors
- Context: checking monitoring-seed candidates after adding supply/매수주체 proxy scoring.
- Error 1: assumed DB path `data/paper_trader.db`; actual DB is repo-root `paper_trader.db` from `app.config.get_settings().database_path`.
- Error 2: nested SSH heredoc quoting stripped quotes around `timeframe=1d`, causing SQLite `unrecognized token: "1d"`.
- Better approach: for remote Python with SQL/string literals, write local script to `/tmp`, scp it, then run remotely; use `get_settings().database_path` or confirmed repo-root DB path.

## 2026-05-11 - supply scout helper patch missed insertion
- Context: wiring investor-flow seed into improvement loop and expanding supply_close_strength_scout scan scope.
- Error: string replacement expected `market_of` with tuple-style suffix check, but file used explicit `or`, so helper functions were not inserted; runtime failed with `NameError: seed_symbols_from`.
- Fix: inserted `read_json` and `seed_symbols_from` with a targeted patch, then reran py_compile and supply scout smoke.
- Practice: after text patches, grep for newly referenced helper definitions before running agent smoke tests.

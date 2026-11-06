#!/usr/bin/env python3
"""Xuất nến đã lưu trong SQLite ra CSV đúng format Kronos fine-tune.

    python scripts/export_training_data.py \
        --db var/kronos_bot.db --symbol BTC/USDT --timeframe 15m \
        --out data/history/BTCUSDT_15m.csv
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

COLUMNS = ["timestamps", "open", "high", "low", "close", "volume", "amount"]


def export(db_path: str, symbol: str, timeframe: str, out_path: str) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """SELECT ts AS timestamps, open, high, low, close, volume, amount
               FROM candles WHERE symbol=? AND timeframe=? ORDER BY ts""",
            con,
            params=(symbol, timeframe),
        )
    finally:
        con.close()

    if df.empty:
        raise SystemExit(f"Không có nến trong DB cho {symbol} {timeframe}")
    df["timestamps"] = pd.to_datetime(df["timestamps"])
    df = df.drop_duplicates("timestamps").sort_values("timestamps").reset_index(drop=True)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8")
    print(f"✅ {len(df):,} nến → {out}")
    print(f"   {df['timestamps'].min()} → {df['timestamps'].max()}")
    return df


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="var/kronos_bot.db")
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--out", default="")
    args = p.parse_args()
    out = args.out or f"data/history/{args.symbol.replace('/', '')}_{args.timeframe}.csv"
    export(args.db, args.symbol, args.timeframe, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

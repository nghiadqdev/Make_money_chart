#!/usr/bin/env python3
"""
Thu thập dữ liệu K-line từ nhiều nguồn và chuẩn hoá về format Kronos.

Output CSV luôn có cột:
    timestamps, open, high, low, close, volume, amount

Cách dùng (chạy từ thư mục gốc dự án):

    # A-share / HK qua AKShare (daily hoặc 1/5/15/30/60 phút)
    python scripts/collect_data.py akshare --symbol 600580 --period 5 \
        --start 2024-01-01 --end 2025-12-31 --out data/600580_5min.csv

    # US stocks / ETF / forex / index qua yfinance
    python scripts/collect_data.py yfinance --symbol AAPL --interval 1d \
        --start 2015-01-01 --end 2025-12-31 --out data/AAPL_1d.csv

    # Crypto qua ccxt (binance, okx, bybit, ...)
    python scripts/collect_data.py ccxt --exchange binance --symbol BTC/USDT \
        --timeframe 5m --start 2020-01-01 --out data/BTCUSDT_5m.csv

    # Crypto số lượng lớn qua Binance Vision (zip theo tháng, không rate-limit)
    python scripts/collect_data.py binance_vision --symbol BTCUSDT --interval 5m \
        --start 2020-01 --end 2025-09 --out data/BTCUSDT_5m.csv

    # Chứng khoán Việt Nam qua vnstock
    python scripts/collect_data.py vnstock --symbol ACB --interval 1D \
        --start 2015-01-01 --end 2025-12-31 --out data/ACB_1d.csv

Cài dependency tương ứng:
    pip install akshare            # akshare
    pip install yfinance           # yfinance
    pip install ccxt               # ccxt
    pip install vnstock            # vnstock
    # binance_vision chỉ cần pandas + requests (có sẵn)
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
import warnings
import zipfile
from datetime import datetime, timezone

import pandas as pd

warnings.filterwarnings("ignore")

OUT_COLS = ["timestamps", "open", "high", "low", "close", "volume", "amount"]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def finalize(df: pd.DataFrame, out_path: str) -> pd.DataFrame:
    """Chuẩn hoá, sắp xếp, khử trùng lặp và ghi CSV (không BOM)."""
    missing = [c for c in ("timestamps", "open", "high", "low", "close") if c not in df.columns]
    if missing:
        raise ValueError(f"Thiếu cột bắt buộc: {missing}. Các cột hiện có: {list(df.columns)}")

    if "volume" not in df.columns:
        df["volume"] = 0.0
    if "amount" not in df.columns:
        # amount không bắt buộc với Kronos; ước lượng theo giá đóng cửa
        df["amount"] = df["volume"] * df["close"]

    df = df[OUT_COLS].copy()
    df["timestamps"] = pd.to_datetime(df["timestamps"], errors="coerce")
    if getattr(df["timestamps"].dt, "tz", None) is not None:
        df["timestamps"] = df["timestamps"].dt.tz_convert("UTC").dt.tz_localize(None)

    for c in ["open", "high", "low", "close", "volume", "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = (
        df.dropna(subset=["timestamps", "open", "high", "low", "close"])
        .drop_duplicates(subset=["timestamps"], keep="last")
        .sort_values("timestamps")
        .reset_index(drop=True)
    )
    df = df[(df["close"] > 0) & (df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0)].copy()

    # Một số nguồn (Yahoo adjusted) có high/low lệch nhẹ do làm tròn.
    # Ép lại để đảm bảo high/low bao trùm open/close.
    price_cols = ["open", "high", "low", "close"]
    df["high"] = df[price_cols].max(axis=1)
    df["low"] = df[price_cols].min(axis=1)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8")

    print(f"\n✅ Đã lưu {len(df):,} dòng → {out_path}")
    print(f"   Khoảng thời gian: {df['timestamps'].min()} → {df['timestamps'].max()}")
    print(f"   Cột: {list(df.columns)}")
    return df


def _rename(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    return df.rename(columns={k: v for k, v in mapping.items() if k in df.columns})


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def _to_sina_symbol(symbol: str) -> str:
    s = symbol.lower().replace(".", "")
    if s.startswith(("sh", "sz", "bj")):
        return s
    if s.startswith(("6", "9", "5")):
        return f"sh{s}"
    if s.startswith(("4", "8")):
        return f"bj{s}"
    return f"sz{s}"


def fetch_akshare(args) -> pd.DataFrame:
    import akshare as ak

    period = args.period
    if getattr(args, "provider", "em") == "sina":
        # Sina: ổn định hơn Đông Tài chính, nhưng dữ liệu phút chỉ ~1970 nến gần nhất
        if period in ("daily", "weekly", "monthly"):
            df = ak.stock_zh_a_daily(
                symbol=_to_sina_symbol(args.symbol),
                start_date=args.start.replace("-", ""),
                end_date=args.end.replace("-", ""),
                adjust=args.adjust or "",
            )
        else:
            df = ak.stock_zh_a_minute(
                symbol=_to_sina_symbol(args.symbol),
                period=period,
                adjust=args.adjust or "",
            )
        df = _rename(df, {"date": "timestamps", "day": "timestamps"})
    elif period in ("daily", "weekly", "monthly"):
        df = ak.stock_zh_a_hist(
            symbol=args.symbol,
            period=period,
            start_date=args.start.replace("-", ""),
            end_date=args.end.replace("-", ""),
            adjust=args.adjust,
        )
    else:
        df = ak.stock_zh_a_hist_min_em(
            symbol=args.symbol,
            period=period,
            start_date=f"{args.start} 09:30:00",
            end_date=f"{args.end} 15:00:00",
            adjust=args.adjust,
        )

    if df is None or df.empty:
        raise RuntimeError(f"AKShare không trả dữ liệu cho {args.symbol} ({period})")

    df = _rename(
        df,
        {
            "日期": "timestamps",
            "时间": "timestamps",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
        },
    )
    return df


def fetch_yfinance(args) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(
        args.symbol,
        start=args.start,
        end=args.end,
        interval=args.interval,
        auto_adjust=True,
        progress=False,
        group_by="column",
    )
    if df is None or df.empty:
        raise RuntimeError(
            f"yfinance không trả dữ liệu cho {args.symbol} ({args.interval}). "
            "Lưu ý giới hạn: 1m ≤ 7 ngày, 5m/15m ≤ 60 ngày, 1h ≤ 730 ngày."
        )

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    df = _rename(
        df,
        {
            "Datetime": "timestamps",
            "Date": "timestamps",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        },
    )
    if "timestamps" in df.columns:
        # index đã reset, nhưng với daily index tên là Date
        pass
    return df


def fetch_ccxt(args) -> pd.DataFrame:
    import ccxt

    exchange_cls = getattr(ccxt, args.exchange, None)
    if exchange_cls is None:
        raise ValueError(f"ccxt không hỗ trợ sàn '{args.exchange}'")

    ex = exchange_cls({"enableRateLimit": True})
    ex.load_markets()

    since = int(pd.Timestamp(args.start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(args.end, tz="UTC").timestamp() * 1000) if args.end else None
    limit = 1000
    rows = []

    print(f"⏳ Tải {args.symbol} {args.timeframe} từ {args.exchange}...")
    while True:
        batch = ex.fetch_ohlcv(args.symbol, timeframe=args.timeframe, since=since, limit=limit)
        if not batch:
            break
        rows.extend(batch)
        last_ts = batch[-1][0]
        if end_ms and last_ts >= end_ms:
            break
        if len(batch) < limit:
            break
        since = last_ts + 1
        time.sleep(ex.rateLimit / 1000)
        if len(rows) % 20000 == 0:
            print(f"   ... {len(rows):,} nến")

    if not rows:
        raise RuntimeError("ccxt không trả dữ liệu. Kiểm tra symbol/timeframe/sàn.")

    df = pd.DataFrame(rows, columns=["ms", "open", "high", "low", "close", "volume"])
    df["timestamps"] = pd.to_datetime(df["ms"], unit="ms", utc=True)
    df = df.drop(columns=["ms"])
    if end_ms:
        df = df[df["timestamps"] <= pd.to_datetime(end_ms, unit="ms", utc=True)]
    return df


def fetch_binance_vision(args) -> pd.DataFrame:
    """Tải file zip tháng từ data.binance.vision (spot klines)."""
    import requests

    symbol = args.symbol.upper()
    start = pd.Period(args.start, freq="M")
    end = pd.Period(args.end, freq="M")
    frames = []

    for period in pd.period_range(start, end, freq="M"):
        name = f"{symbol}-{args.interval}-{period.year}-{period.month:02d}"
        url = (
            "https://data.binance.vision/data/spot/monthly/klines/"
            f"{symbol}/{args.interval}/{name}.zip"
        )
        print(f"   ↓ {name}.zip")
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404:
            print(f"     (không có {name}, bỏ qua)")
            continue
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_name = zf.namelist()[0]
            with zf.open(csv_name) as fh:
                raw = pd.read_csv(fh, header=None)
        # Cột chuẩn của Binance: open_time, open, high, low, close, volume, close_time, quote_volume, ...
        if raw.shape[1] < 9:
            print(f"     (bỏ qua {name}: chỉ có {raw.shape[1]} cột)")
            continue
        raw = raw.iloc[:, :9]
        raw.columns = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "amount", "n_trades",
        ]
        # Một số file cũ dùng ms, file mới dùng microsecond -> xử lý từng file
        ts = pd.to_numeric(raw["open_time"], errors="coerce")
        raw = raw[ts.notna()].copy()
        ts = ts[ts.notna()]
        unit = "us" if ts.max() > 10**15 else "ms"
        raw["timestamps"] = pd.to_datetime(ts.astype("int64"), unit=unit, utc=True)
        frames.append(raw[["timestamps", "open", "high", "low", "close", "volume", "amount"]])

    if not frames:
        raise RuntimeError("Không tải được file nào từ Binance Vision.")

    return pd.concat(frames, ignore_index=True)


def fetch_vnstock(args) -> pd.DataFrame:
    from vnstock import Vnstock

    stock = Vnstock().stock(symbol=args.symbol.upper(), source=args.source)
    df = stock.quote.history(start=args.start, end=args.end, interval=args.interval)
    if df is None or df.empty:
        raise RuntimeError(f"vnstock không trả dữ liệu cho {args.symbol}")

    df = _rename(
        df,
        {
            "time": "timestamps",
            "date": "timestamps",
            "tradingDate": "timestamps",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
            "value": "amount",
        },
    )
    return df


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Thu thập dữ liệu K-line chuẩn hoá cho Kronos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="source", required=True)

    a = sub.add_parser("akshare", help="A-share / HK / ETF qua AKShare")
    a.add_argument("--symbol", required=True, help="Mã, ví dụ 600580 / 000001")
    a.add_argument("--period", default="daily", choices=["daily", "weekly", "monthly", "1", "5", "15", "30", "60"])
    a.add_argument("--start", required=True, help="YYYY-MM-DD")
    a.add_argument("--end", required=True, help="YYYY-MM-DD")
    a.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"])
    a.add_argument("--provider", default="sina", choices=["sina", "em"],
                   help="sina (ổn định, phút chỉ ~1970 nến) hoặc em (Đông Tài chính, dễ bị rate-limit)")
    a.add_argument("--out", required=True)

    y = sub.add_parser("yfinance", help="US stocks / ETF / forex / index")
    y.add_argument("--symbol", required=True, help="AAPL, MSFT, EURUSD=X, ^GSPC ...")
    y.add_argument("--interval", default="1d", help="1m,5m,15m,30m,1h,1d,1wk,1mo")
    y.add_argument("--start", required=True, help="YYYY-MM-DD")
    y.add_argument("--end", default=None, help="YYYY-MM-DD")
    y.add_argument("--out", required=True)

    c = sub.add_parser("ccxt", help="Crypto qua ccxt (binance, okx, bybit, ...)")
    c.add_argument("--exchange", default="binance")
    c.add_argument("--symbol", required=True, help="BTC/USDT")
    c.add_argument("--timeframe", default="5m", help="1m,5m,15m,1h,4h,1d ...")
    c.add_argument("--start", required=True, help="YYYY-MM-DD")
    c.add_argument("--end", default=None, help="YYYY-MM-DD")
    c.add_argument("--out", required=True)

    b = sub.add_parser("binance_vision", help="Crypto số lượng lớn qua Binance Vision")
    b.add_argument("--symbol", required=True, help="BTCUSDT")
    b.add_argument("--interval", default="5m", help="1m,5m,15m,1h,4h,1d ...")
    b.add_argument("--start", required=True, help="YYYY-MM")
    b.add_argument("--end", required=True, help="YYYY-MM")
    b.add_argument("--out", required=True)

    v = sub.add_parser("vnstock", help="Chứng khoán Việt Nam qua vnstock")
    v.add_argument("--symbol", required=True, help="ACB, VNM, FPT ...")
    v.add_argument("--interval", default="1D", help="1m,5m,15m,30m,1H,1D,1W")
    v.add_argument("--start", required=True, help="YYYY-MM-DD")
    v.add_argument("--end", default=None, help="YYYY-MM-DD")
    v.add_argument("--source", default="VCI", choices=["VCI", "TCBS", "MSN"])
    v.add_argument("--out", required=True)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "akshare": fetch_akshare,
        "yfinance": fetch_yfinance,
        "ccxt": fetch_ccxt,
        "binance_vision": fetch_binance_vision,
        "vnstock": fetch_vnstock,
    }
    try:
        df = handlers[args.source](args)
        finalize(df, args.out)
    except ImportError as exc:
        print(f"❌ Thiếu thư viện: {exc}. Hãy pip install thư viện tương ứng.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"❌ Lỗi: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

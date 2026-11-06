#!/usr/bin/env python3
"""Smoke test: chạy Kronos trên một file CSV trong data/ và đối chiếu với giá thực.

    python scripts/smoke_test.py --data data/BTCUSDT_5m_2022-2025.csv
    python scripts/smoke_test.py --data data/vn/FPT_1d.csv --pred-len 30
"""

import argparse
import sys
import warnings

import numpy as np
import pandas as pd
import torch

warnings.filterwarnings("ignore")
sys.path.append(".")

from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402


def pick_device(requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda:0"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--model", default="NeoQuasar/Kronos-small")
    p.add_argument("--tokenizer", default="NeoQuasar/Kronos-Tokenizer-base")
    p.add_argument("--device", default="auto")
    p.add_argument("--lookback", type=int, default=400)
    p.add_argument("--pred-len", type=int, default=120)
    p.add_argument("--sample-count", type=int, default=1)
    p.add_argument("--temperature", type=float, default=1.0)
    p.add_argument("--top-p", type=float, default=0.9)
    args = p.parse_args()

    df = pd.read_csv(args.data)
    df["timestamps"] = pd.to_datetime(df["timestamps"])
    df = df.sort_values("timestamps").reset_index(drop=True)

    need = args.lookback + args.pred_len
    if len(df) < need:
        print(f"❌ Cần ít nhất {need} dòng, file chỉ có {len(df)}")
        return 1

    cols = [c for c in ["open", "high", "low", "close", "volume", "amount"] if c in df.columns]
    device = pick_device(args.device)
    print(f"📄 {args.data}: {len(df):,} dòng")
    print(f"🖥  device: {device} | lookback={args.lookback} pred_len={args.pred_len}")

    tokenizer = KronosTokenizer.from_pretrained(args.tokenizer)
    model = Kronos.from_pretrained(args.model)
    predictor = KronosPredictor(model, tokenizer, device=device, max_context=512)

    x_df = df.iloc[: args.lookback][cols]
    x_ts = df.iloc[: args.lookback]["timestamps"]
    y_ts = df.iloc[args.lookback: args.lookback + args.pred_len]["timestamps"]

    pred = predictor.predict(
        df=x_df,
        x_timestamp=x_ts,
        y_timestamp=y_ts,
        pred_len=args.pred_len,
        T=args.temperature,
        top_p=args.top_p,
        sample_count=args.sample_count,
        verbose=True,
    )

    actual = df.iloc[args.lookback: args.lookback + args.pred_len]
    mae = float(np.mean(np.abs(pred["close"].values - actual["close"].values)))
    mape = float(np.mean(np.abs(pred["close"].values - actual["close"].values) / actual["close"].values) * 100)

    print("\n✅ Dự đoán thành công")
    print(pred.head(3).to_string())
    print(f"\n📉 MAE close = {mae:.6g} | MAPE = {mape:.2f}%")
    print(f"   Giá thực tế cuối: {actual['close'].iloc[-1]:.6g} | dự đoán cuối: {pred['close'].iloc[-1]:.6g}")
    assert pred.shape == (args.pred_len, len(pred.columns))
    assert not pred.isna().any().any()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

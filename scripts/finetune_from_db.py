#!/usr/bin/env python3
"""Chuẩn bị và (tuỳ chọn) chạy fine-tune từ lịch sử nến trong SQLite.

Ví dụ chỉ xuất dataset + tạo config:
    python scripts/finetune_from_db.py --symbol BTC/USDT --timeframe 15m --epochs 3

Chạy luôn fine-tune (có thể rất lâu trên CPU):
    python scripts/finetune_from_db.py --symbol BTC/USDT --timeframe 15m \
        --epochs 3 --run
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.export_training_data import export  # noqa: E402


def build_config(args, csv_path: Path, exp_name: str) -> dict:
    base_path = (REPO_ROOT / "finetune_csv" / "finetuned").resolve()
    return {
        "data": {
            "data_path": str(csv_path.resolve()),
            "lookback_window": args.lookback,
            "predict_window": args.predict,
            "max_context": 512,
            "clip": 5.0,
            "train_ratio": 0.9,
            "val_ratio": 0.1,
            "test_ratio": 0.0,
        },
        "training": {
            "tokenizer_epochs": args.epochs,
            "basemodel_epochs": args.epochs,
            "batch_size": args.batch_size,
            "log_interval": 50,
            "num_workers": 2,
            "seed": 42,
            "tokenizer_learning_rate": 0.0002,
            "predictor_learning_rate": 0.00004,
            "adam_beta1": 0.9,
            "adam_beta2": 0.95,
            "adam_weight_decay": 0.1,
            "accumulation_steps": 1,
        },
        "model_paths": {
            "pretrained_tokenizer": args.pretrained_tokenizer,
            "pretrained_predictor": args.pretrained_predictor,
            "exp_name": exp_name,
            "base_path": str(base_path),
            "base_save_path": "",
            "finetuned_tokenizer": "",
            "tokenizer_save_name": "tokenizer",
            "basemodel_save_name": "basemodel",
        },
        "experiment": {
            "name": "kronos_from_db",
            "description": f"Fine-tune from local SQLite history: {args.symbol} {args.timeframe}",
            "use_comet": False,
            "train_tokenizer": True,
            "train_basemodel": True,
            "skip_existing": False,
        },
        "device": {
            "use_cuda": False,  # đổi thành true nếu có GPU CUDA
            "device_id": 0,
        },
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="var/kronos_bot.db")
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--out", default="")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--lookback", type=int, default=512)
    p.add_argument("--predict", type=int, default=48)
    p.add_argument("--max-rows", type=int, default=0, help="0 = dùng toàn bộ")
    p.add_argument("--pretrained-tokenizer", default="NeoQuasar/Kronos-Tokenizer-base")
    p.add_argument("--pretrained-predictor", default="NeoQuasar/Kronos-small")
    p.add_argument("--run", action="store_true", help="Chạy train_sequential.py sau khi tạo config")
    args = p.parse_args()

    slug = args.symbol.replace("/", "") + "_" + args.timeframe
    out = args.out or f"data/history/{slug}.csv"
    df = export(args.db, args.symbol, args.timeframe, out)
    if args.max_rows and len(df) > args.max_rows:
        df = df.iloc[-args.max_rows:].reset_index(drop=True)
        Path(out).write_text(df.to_csv(index=False), encoding="utf-8")
        print(f"   (giới hạn còn {len(df):,} nến để test)")

    if len(df) < 2000:
        print("⚠️  Ít hơn 2.000 nến; fine-tune có thể không ổn định.")

    exp_name = f"auto_{slug}_{args.epochs}ep"
    config_path = REPO_ROOT / "finetune_csv" / "configs" / f"{exp_name}.yaml"
    config = build_config(args, Path(out), exp_name)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    print(f"\n✅ Config: {config_path}")
    print(f"   Dataset: {out} ({len(df):,} nến)")
    print("\nChạy fine-tune:")
    print(f"  cd {REPO_ROOT}/finetune_csv")
    print(f"  python train_sequential.py --config configs/{config_path.name}")

    if args.run:
        print("\n▶ Bắt đầu fine-tune...")
        return subprocess.call(
            [sys.executable, "train_sequential.py", "--config", str(config_path)],
            cwd=str(REPO_ROOT / "finetune_csv"),
        )

    print(
        "\nSau khi train xong, trỏ config bot vào model mới:\n"
        f"  tokenizer_id: {config['model_paths']['base_path']}/{exp_name}/tokenizer/best_model\n"
        f"  model_id:     {config['model_paths']['base_path']}/{exp_name}/basemodel/best_model"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

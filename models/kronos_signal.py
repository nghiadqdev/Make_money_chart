"""Kronos inference wrapper that turns sampled price paths into probabilities.

Kronos is a generative price model, not a classifier.  We adapt it without
retraining by drawing many independent Monte-Carlo paths for the same context
and estimating:

    p_up   = P(path return > +fee_buffer)
    p_down = P(path return < -fee_buffer)
    score  = p_up - p_down

This is deliberately conservative: a move must beat fees + slippage buffer to
count as a directional outcome.
"""

from __future__ import annotations

import threading
from typing import Iterable, List, Sequence

import numpy as np
import pandas as pd

from config.loader import ModelConfig
from core.logging_config import get_logger
from core.timeframes import pandas_freq
from core.types import ModelPrediction

logger = get_logger("kronos")


class ModelInferenceError(RuntimeError):
    pass


def classify_returns(
    returns: Sequence[float],
    fee_buffer: float,
    quantiles: Iterable[float] = (0.05, 0.25, 0.5, 0.75, 0.95),
) -> ModelPrediction:
    """Pure, testable probability classifier over path returns."""
    arr = np.asarray(list(returns), dtype=float)
    if arr.size == 0:
        raise ModelInferenceError("Không có path return để phân loại")

    p_up = float(np.mean(arr > fee_buffer))
    p_down = float(np.mean(arr < -fee_buffer))
    expected = float(np.mean(arr))
    q = {f"q{int(q * 100):02d}": float(np.quantile(arr, q)) for q in quantiles}

    return ModelPrediction(
        p_up=p_up,
        p_down=p_down,
        expected_return=expected,
        score=p_up - p_down,
        confidence=max(p_up, p_down),
        last_price=0.0,  # caller fills this
        path_returns=arr.tolist(),
        quantiles=q,
    )


class KronosSignalModel:
    def __init__(self, config: ModelConfig, timeframe: str = "1h"):
        self.config = config
        self.timeframe = timeframe
        self._lock = threading.Lock()
        self._predictor = None
        self._torch = None
        self._load()

    # ------------------------------------------------------------------ #
    # Lazy-ish model loading
    # ------------------------------------------------------------------ #
    def _pick_device(self) -> str:
        if self.config.device != "auto":
            return self.config.device
        import torch

        if torch.cuda.is_available():
            return "cuda:0"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _load(self) -> None:
        import torch
        from model import Kronos, KronosPredictor, KronosTokenizer

        self._torch = torch
        device = self._pick_device()
        logger.info("Loading %s + %s on %s", self.config.model_id, self.config.tokenizer_id, device)

        tokenizer = KronosTokenizer.from_pretrained(self.config.tokenizer_id)
        model = Kronos.from_pretrained(self.config.model_id)
        self._predictor = KronosPredictor(
            model,
            tokenizer,
            device=device,
            max_context=self.config.max_context,
        )
        logger.info("Kronos ready | lookback=%d pred_len=%d paths=%d",
                    self.config.lookback, self.config.pred_len, self.config.n_paths)

    # ------------------------------------------------------------------ #
    # Inference
    # ------------------------------------------------------------------ #
    def infer(self, df: pd.DataFrame, lookback: int | None = None) -> ModelPrediction:
        if self._predictor is None:
            raise ModelInferenceError("Model chưa được load")

        effective_lookback = int(lookback or self.config.lookback)
        effective_lookback = max(50, min(effective_lookback, self.config.max_context))

        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ModelInferenceError(f"Thiếu cột OHLCV: {sorted(missing)}")

        data = (
            df.copy()
            .sort_values("timestamp")
            .drop_duplicates("timestamp", keep="last")
            .reset_index(drop=True)
        )
        if len(data) < effective_lookback:
            raise ModelInferenceError(
                f"Cần {effective_lookback} nến, chỉ có {len(data)}"
            )
        if data[["open", "high", "low", "close", "volume"]].isna().any().any():
            raise ModelInferenceError("Dữ liệu OHLCV có NaN")

        window = data.iloc[-effective_lookback:].copy()
        last_price = float(window["close"].iloc[-1])
        last_ts = pd.Timestamp(window["timestamp"].iloc[-1])

        x_df = window[["open", "high", "low", "close", "volume"] + (
            ["amount"] if "amount" in window.columns else []
        )].reset_index(drop=True)
        x_ts = window["timestamp"].reset_index(drop=True)
        y_ts = pd.Series(
            pd.date_range(
                start=last_ts,
                periods=self.config.pred_len + 1,
                freq=pandas_freq(self.timeframe),
            )[1:]
        )

        # Each copy is an independent sample; batching keeps GPU/MPS busy.
        x_list = [x_df.copy() for _ in range(self.config.n_paths)]
        x_ts_list = [x_ts.copy() for _ in range(self.config.n_paths)]
        y_ts_list = [y_ts.copy() for _ in range(self.config.n_paths)]

        with self._lock:
            try:
                preds = self._predictor.predict_batch(
                    df_list=x_list,
                    x_timestamp_list=x_ts_list,
                    y_timestamp_list=y_ts_list,
                    pred_len=self.config.pred_len,
                    T=self.config.temperature,
                    top_p=self.config.top_p,
                    sample_count=1,
                    verbose=False,
                )
            except Exception as exc:
                raise ModelInferenceError(f"Kronos inference lỗi: {exc}") from exc

        returns: List[float] = []
        for pred in preds:
            if pred is None or len(pred) == 0:
                continue
            final_close = float(pred["close"].iloc[-1])
            if final_close > 0:
                returns.append(final_close / last_price - 1.0)

        prediction = classify_returns(returns, self.config.fee_buffer_pct)
        prediction = ModelPrediction(
            p_up=prediction.p_up,
            p_down=prediction.p_down,
            expected_return=prediction.expected_return,
            score=prediction.score,
            confidence=prediction.confidence,
            last_price=last_price,
            path_returns=prediction.path_returns,
            quantiles=prediction.quantiles,
        )
        logger.info(
            "infer %s | lookback=%d price=%.4f p_up=%.3f p_down=%.3f exp=%+.4f%% score=%+.3f",
            last_ts, effective_lookback, last_price, prediction.p_up,
            prediction.p_down, prediction.expected_return * 100, prediction.score,
        )
        return prediction

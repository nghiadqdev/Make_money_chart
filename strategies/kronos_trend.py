"""Kronos probability + trend-filter strategy."""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from config.loader import ModelConfig, StrategyConfig
from core.logging_config import get_logger
from core.types import ModelPrediction, SignalType, TradeSignal

logger = get_logger("strategy")


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return float("nan")
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    return float(atr.iloc[-1])


class KronosTrendStrategy:
    def __init__(self, model_config: ModelConfig, strategy_config: StrategyConfig, symbol: str = "BTC/USDT"):
        self.model_config = model_config
        self.config = strategy_config
        self.symbol = symbol
        self.long_threshold = model_config.long_threshold
        self.short_threshold = model_config.short_threshold

    def set_thresholds(self, long_threshold: float, short_threshold: float) -> None:
        """Cập nhật ngưỡng từ calibration (không thay đổi trọng số model)."""
        self.long_threshold = float(long_threshold)
        self.short_threshold = float(short_threshold)

    def _trend(self, df: pd.DataFrame) -> tuple[bool, float, float]:
        close = df["close"].astype(float)
        if len(close) < max(self.config.ema_fast, self.config.ema_slow):
            return False, float("nan"), float("nan")
        ema_fast = float(close.ewm(span=self.config.ema_fast, adjust=False).mean().iloc[-1])
        ema_slow = float(close.ewm(span=self.config.ema_slow, adjust=False).mean().iloc[-1])
        return ema_fast > ema_slow, ema_fast, ema_slow

    def generate(self, df: pd.DataFrame, prediction: ModelPrediction) -> TradeSignal:
        price = float(prediction.last_price or df["close"].iloc[-1])
        atr = compute_atr(df, self.config.atr_period)
        ema_fast = ema_slow = float("nan")
        trend_up = trend_down = False

        if not np.isnan(atr) and self.config.require_trend_alignment:
            trend_up, ema_fast, ema_slow = self._trend(df)
            trend_down = (not trend_up) and ema_fast < ema_slow and price < ema_slow
            trend_up = trend_up and price > ema_slow

        reasons: List[str] = [
            f"p_up={prediction.p_up:.3f}",
            f"p_down={prediction.p_down:.3f}",
            f"exp={prediction.expected_return:+.4%}",
            f"score={prediction.score:+.3f}",
            f"atr={atr:.4f}" if not np.isnan(atr) else "atr=nan",
        ]

        if np.isnan(atr) or atr <= 0:
            reasons.append("ATR không hợp lệ -> hold")
            return self._signal("HOLD", price, atr, prediction, reasons)

        long_ok = (
            prediction.p_up >= self.long_threshold
            and prediction.expected_return >= self.model_config.min_expected_return
        )
        short_ok = (
            prediction.p_down >= (1.0 - self.short_threshold)
            and prediction.expected_return <= -self.model_config.min_expected_return
        )

        if self.config.require_trend_alignment:
            long_ok = long_ok and trend_up
            short_ok = short_ok and trend_down
            reasons.append(f"ema_fast={ema_fast:.4f} ema_slow={ema_slow:.4f}")

        if long_ok and not short_ok:
            reasons.append("long: probability + trend aligned")
            return self._signal("LONG", price, atr, prediction, reasons)
        if short_ok and not long_ok:
            reasons.append("short: probability + trend aligned")
            return self._signal("SHORT", price, atr, prediction, reasons)

        if long_ok and short_ok:
            reasons.append("xung đột long/short -> hold")
        else:
            reasons.append("không đạt ngưỡng xác suất/trend -> hold")
        return self._signal("HOLD", price, atr, prediction, reasons)

    def _signal(
        self,
        signal: str,
        price: float,
        atr: float,
        prediction: ModelPrediction,
        reasons: List[str],
    ) -> TradeSignal:
        return TradeSignal(
            symbol=self.symbol,
            signal=SignalType(signal.lower()),
            price=price,
            atr=atr,
            score=prediction.score,
            confidence=prediction.confidence,
            p_up=prediction.p_up,
            p_down=prediction.p_down,
            expected_return=prediction.expected_return,
            reasons=reasons,
            timestamp=prediction.created_at,
        )

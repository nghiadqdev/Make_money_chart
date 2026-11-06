"""Forecast logging and outcome evaluation.

Every inference is stored in SQLite with its horizon. When the horizon has
passed, the bot finds the actual close from its local candle store and records
direction hit, Brier score and absolute error. This gives an honest feedback
loop without pretending the frozen Kronos weights are being updated online.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict

import pandas as pd

from config.loader import ModelConfig
from core.database import Database
from core.logging_config import get_logger
from core.timeframes import timeframe_delta
from core.types import ModelPrediction, TradeSignal

logger = get_logger("forecast_tracker")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ForecastTracker:
    def __init__(self, db: Database, model_config: ModelConfig, fee_buffer: float):
        self.db = db
        self.model_config = model_config
        self.fee_buffer = fee_buffer

    def horizon_end(self, last_timestamp, pred_len: int, timeframe: str) -> str:
        delta = timeframe_delta(timeframe) * int(pred_len)
        return (pd.Timestamp(last_timestamp) + delta).isoformat()

    def record_forecast(
        self,
        df: pd.DataFrame,
        prediction: ModelPrediction,
        signal: TradeSignal,
        decision: str,
        lookback: int,
        timeframe: str,
        threshold_long: float,
        threshold_short: float,
        decision_reason: str = "",
    ) -> int:
        last_ts = pd.Timestamp(df["timestamp"].iloc[-1])
        horizon = self.horizon_end(last_ts, self.model_config.pred_len, timeframe)
        data = {
            "created_at": prediction.created_at,
            "symbol": signal.symbol,
            "timeframe": timeframe,
            "lookback": int(lookback),
            "pred_len": int(self.model_config.pred_len),
            "last_price": float(prediction.last_price),
            "predicted_price": float(prediction.last_price * (1.0 + prediction.expected_return)),
            "expected_return": float(prediction.expected_return),
            "p_up": float(prediction.p_up),
            "p_down": float(prediction.p_down),
            "score": float(prediction.score),
            "confidence": float(prediction.confidence),
            "signal": signal.signal.value,
            "decision": decision,
            "decision_reason": decision_reason,
            "threshold_long": float(threshold_long),
            "threshold_short": float(threshold_short),
            "horizon_end": horizon,
        }
        forecast_id = self.db.insert_forecast(data)
        logger.info(
            "forecast #%d stored | horizon=%s lookback=%d p_up=%.3f",
            forecast_id, horizon, lookback, prediction.p_up,
        )
        return forecast_id

    def evaluate_pending(self, symbol: str, timeframe: str, limit: int = 100) -> int:
        pending = self.db.pending_forecasts(_now_iso(), limit=limit)
        evaluated = 0
        now_ts = pd.Timestamp.now(tz="UTC").tz_localize(None)
        for item in pending:
            if item["symbol"] != symbol or item["timeframe"] != timeframe:
                continue
            # Chỉ đánh giá sau khi nến tại horizon đã đóng.
            horizon_close = pd.Timestamp(item["horizon_end"]) + timeframe_delta(item["timeframe"])
            if now_ts < horizon_close:
                continue
            candle = self.db.get_candle_at_or_after(symbol, timeframe, item["horizon_end"])
            if not candle:
                continue

            last_price = float(item["last_price"])
            actual_price = float(candle["close"])
            if last_price <= 0 or actual_price <= 0:
                continue
            actual_return = actual_price / last_price - 1.0
            actual_up = actual_return > 0.0
            direction_up = float(item["p_up"]) >= float(item["p_down"])
            correct = (
                actual_return > self.fee_buffer
                if direction_up
                else actual_return < -self.fee_buffer
            )
            brier = (float(item["p_up"]) - (1.0 if actual_up else 0.0)) ** 2
            abs_error = abs(float(item["expected_return"]) - actual_return)

            self.db.update_forecast_eval(
                int(item["id"]), actual_price, actual_return,
                direction_up, bool(correct), brier, abs_error, _now_iso(),
            )
            evaluated += 1

        if evaluated:
            logger.info("Đã đánh giá %d forecast đến hạn", evaluated)
        return evaluated

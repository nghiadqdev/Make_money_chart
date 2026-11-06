import pandas as pd
import pytest

from config.loader import LearningConfig, ModelConfig
from core.calibration import CalibrationManager
from core.database import Database
from core.forecast_tracker import ForecastTracker
from core.types import ModelPrediction, SignalType, TradeSignal


def make_prediction(created_at="2026-01-01T09:45:00+00:00"):
    return ModelPrediction(
        p_up=0.8, p_down=0.2, expected_return=0.01, score=0.6,
        confidence=0.8, last_price=100.0, path_returns=[0.01],
        created_at=created_at,
    )


def make_signal():
    return TradeSignal(
        symbol="BTC/USDT", signal=SignalType.LONG, price=100.0, atr=2.0,
        score=0.6, confidence=0.8, p_up=0.8, p_down=0.2,
        expected_return=0.01, timestamp="2026-01-01T09:45:00+00:00",
    )


def test_forecast_is_logged_and_evaluated(tmp_path):
    db = Database(tmp_path / "test.db")
    model_cfg = ModelConfig(pred_len=1)
    tracker = ForecastTracker(db, model_cfg, fee_buffer=0.0025)
    ts = pd.Timestamp("2026-01-01T09:45:00")
    df = pd.DataFrame([{
        "timestamp": ts, "open": 100, "high": 101, "low": 99,
        "close": 100, "volume": 1, "amount": 100,
    }])

    fid = tracker.record_forecast(
        df, make_prediction(), make_signal(), "approved", 400, "15m",
        threshold_long=0.62, threshold_short=0.38, decision_reason="approved",
    )
    assert fid == 1

    # Candle at horizon (10:00) with +2% close.
    future = pd.DataFrame([{
        "timestamp": pd.Timestamp("2026-01-01T10:00:00"), "open": 100,
        "high": 103, "low": 100, "close": 102, "volume": 1, "amount": 102,
    }])
    db.upsert_candles(future, "BTC/USDT", "15m")
    assert tracker.evaluate_pending("BTC/USDT", "15m") == 1

    stats = db.forecast_stats()
    assert stats["evaluated"] == 1
    assert stats["hit_rate"] == pytest.approx(1.0)
    assert stats["brier"] == pytest.approx(0.04)
    assert db.recent_forecasts()[0]["actual_return"] == pytest.approx(0.02)
    db.close()


def test_calibration_raises_threshold_when_precision_low(tmp_path):
    db = Database(tmp_path / "test.db")
    model_cfg = ModelConfig(long_threshold=0.62, short_threshold=0.38, pred_len=1, lookback=400)
    learning = LearningConfig(
        min_samples=5, window=100, min_threshold=0.55,
        max_threshold=0.75, step=0.01,
        calibration_path=str(tmp_path / "calibration.json"),
    )
    # Simulate 5 high-confidence long forecasts that all lost.
    for i in range(5):
        db.conn.execute(
            """INSERT INTO forecasts
               (created_at,symbol,timeframe,lookback,pred_len,last_price,predicted_price,
                expected_return,p_up,p_down,score,confidence,signal,actual_return,
                direction_up,direction_correct,brier,abs_error,evaluated_at,horizon_end)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("2026-01-01", "BTC/USDT", "15m", 400, 1, 100, 101, 0.01, 0.8, 0.2,
             0.6, 0.8, "long", -0.01, 1, 0, 0.64, 0.02, "2026-01-01",
             "2026-01-01T10:00:00"),
        )
    db.conn.commit()
    mgr = CalibrationManager(learning, model_cfg)
    snap = mgr.maybe_update(db)
    assert snap["long_threshold"] == pytest.approx(0.63)
    db.close()

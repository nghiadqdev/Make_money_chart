"""Timeframe helpers shared by inference, forecasting and evaluation."""

from __future__ import annotations

import pandas as pd

_PANDAS_FREQ = {"15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1D"}
_DELTA = {
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    "1h": pd.Timedelta(hours=1),
    "4h": pd.Timedelta(hours=4),
    "1d": pd.Timedelta(hours=24),
}


def pandas_freq(timeframe: str) -> str:
    return _PANDAS_FREQ.get(timeframe, timeframe)


def timeframe_delta(timeframe: str) -> pd.Timedelta:
    if timeframe not in _DELTA:
        raise ValueError(f"Timeframe không hỗ trợ: {timeframe}")
    return _DELTA[timeframe]

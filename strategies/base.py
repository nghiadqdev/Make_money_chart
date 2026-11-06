"""Strategy interface."""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from core.types import ModelPrediction, TradeSignal


class Strategy(Protocol):
    def generate(self, df: pd.DataFrame, prediction: ModelPrediction) -> TradeSignal:
        ...

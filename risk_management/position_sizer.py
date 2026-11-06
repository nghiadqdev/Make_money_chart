"""Volatility-aware position sizing with hard tranche caps."""

from __future__ import annotations

from typing import Optional, Tuple

from config.loader import TradingConfig
from core.types import Side, TradePlan, TradeSignal


class PositionSizer:
    def __init__(self, config: TradingConfig):
        self.config = config

    def build_plan(
        self,
        side: Side,
        equity: float,
        free_cash: float,
        entry_price: float,
        atr: float,
        symbol: str,
        signal: Optional[TradeSignal] = None,
    ) -> Tuple[Optional[TradePlan], str]:
        if entry_price <= 0 or atr <= 0 or equity <= 0:
            return None, "giá/ATR/equity không hợp lệ"

        stop_distance = atr * self.config.sl_atr_mult
        min_stop = entry_price * self.config.min_sl_pct
        max_stop = entry_price * self.config.max_sl_pct
        stop_distance = max(min_stop, min(max_stop, stop_distance))
        take_distance = atr * self.config.tp_atr_mult
        if take_distance <= 0:
            return None, "take-profit distance không hợp lệ"

        # Risk-based size: lose at most risk_per_trade_pct of equity at SL.
        risk_amount = equity * self.config.risk_per_trade_pct
        qty_by_risk = risk_amount / stop_distance
        notional_by_risk = qty_by_risk * entry_price

        # Hard cap: never allocate more than one tranche.
        tranche_cap = equity / max(1, self.config.tranches)
        fee_rate = self.config.taker_fee
        max_notional = min(tranche_cap, free_cash / (1.0 + fee_rate))
        notional = min(notional_by_risk, max_notional)

        if notional < self.config.min_notional:
            return None, (
                f"notional {notional:.4f} < min_notional {self.config.min_notional:.4f} "
                f"(tranche_cap={tranche_cap:.4f}, free_cash={free_cash:.4f})"
            )

        qty = notional / entry_price
        if side is Side.LONG:
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + take_distance
        else:
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - take_distance

        if stop_loss <= 0 or take_profit <= 0:
            return None, "SL/TP không hợp lệ"

        plan = TradePlan(
            symbol=symbol,
            side=side,
            entry_price=float(entry_price),
            qty=float(qty),
            notional=float(notional),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            stop_distance=float(stop_distance),
            risk_amount=float(risk_amount),
            r_multiple=float(take_distance / stop_distance),
            reason=(
                f"risk={risk_amount:.4f} USDT, stop={stop_distance:.4f} "
                f"({stop_distance / entry_price:.2%}), tranche_cap={tranche_cap:.4f}"
            ),
            signal=signal,
        )
        return plan, "ok"

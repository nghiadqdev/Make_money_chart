"""Risk orchestration: circuit breaker + tranche sizing + exposure checks."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from config.loader import TradingConfig
from core.logging_config import get_logger
from core.types import (
    ClosedTrade,
    PortfolioSnapshot,
    RiskDecision,
    Side,
    SignalType,
    TradeSignal,
)
from risk_management.circuit_breaker import CircuitBreaker
from risk_management.position_sizer import PositionSizer

logger = get_logger("risk")


class RiskManager:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.sizer = PositionSizer(config)
        self.breaker = CircuitBreaker(config)

    def evaluate(
        self,
        signal: TradeSignal,
        snapshot: PortfolioSnapshot,
        open_symbols: Iterable[str] = (),
        now: Optional[datetime] = None,
    ) -> RiskDecision:
        checks: Dict[str, Any] = {
            "signal": signal.signal.value,
            "equity": snapshot.equity,
            "free_cash": snapshot.free_cash,
            "open_positions": snapshot.open_positions,
            "drawdown_pct": snapshot.drawdown_pct,
        }

        if signal.signal is SignalType.HOLD:
            return RiskDecision(False, "signal=hold", checks=checks)
        if signal.signal is SignalType.SHORT and not self.config.allow_short:
            return RiskDecision(False, "short bị tắt trong config", checks=checks)
        if snapshot.open_positions >= self.config.max_open_positions:
            return RiskDecision(
                False,
                f"max_open_positions={self.config.max_open_positions} đã đạt",
                checks=checks,
            )
        if signal.symbol in set(open_symbols):
            return RiskDecision(False, f"đã có position cho {signal.symbol}", checks=checks)

        can_trade, breaker_reason = self.breaker.can_trade(now, snapshot.equity)
        checks["circuit_breaker"] = breaker_reason
        if not can_trade:
            return RiskDecision(False, breaker_reason, checks=checks)

        side = Side.LONG if signal.signal is SignalType.LONG else Side.SHORT
        plan, reason = self.sizer.build_plan(
            side=side,
            equity=snapshot.equity,
            free_cash=snapshot.free_cash,
            entry_price=signal.price,
            atr=signal.atr,
            symbol=signal.symbol,
            signal=signal,
        )
        checks["sizing"] = reason
        if plan is None:
            return RiskDecision(False, reason, checks=checks)

        checks.update(
            {
                "notional": plan.notional,
                "qty": plan.qty,
                "stop_loss": plan.stop_loss,
                "take_profit": plan.take_profit,
                "risk_amount": plan.risk_amount,
                "r_multiple": plan.r_multiple,
                "tranche_cap": snapshot.equity / max(1, self.config.tranches),
            }
        )
        return RiskDecision(True, "approved", plan=plan, checks=checks)

    def on_equity(self, equity: float) -> None:
        self.breaker.update_equity(equity)

    def on_trade_closed(self, trade: ClosedTrade, now: Optional[datetime] = None) -> None:
        self.breaker.record_trade(trade.pnl, now)

    def circuit_status(self) -> Dict[str, Any]:
        return self.breaker.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        return self.breaker.snapshot()

    def restore(self, data: Optional[Dict[str, Any]]) -> None:
        self.breaker.restore(data)

    def reset(self) -> None:
        self.breaker.reset()

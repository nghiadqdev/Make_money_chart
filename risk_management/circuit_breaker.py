"""Circuit breaker: consecutive losses, cooldown and max drawdown."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from config.loader import TradingConfig
from core.logging_config import get_logger

logger = get_logger("risk.circuit")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CircuitBreaker:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.consecutive_losses = 0
        self.cooldown_until: Optional[datetime] = None
        self.drawdown_tripped = False
        self.peak_equity = 0.0

    def update_equity(self, equity: float) -> None:
        if equity > self.peak_equity:
            self.peak_equity = equity
        if self.peak_equity > 0:
            drawdown = 1.0 - equity / self.peak_equity
            if drawdown >= self.config.max_drawdown_pct:
                if not self.drawdown_tripped:
                    logger.error(
                        "CIRCUIT BREAKER: drawdown %.2f%% >= %.2f%% -> halt",
                        drawdown * 100, self.config.max_drawdown_pct * 100,
                    )
                self.drawdown_tripped = True

    def record_trade(self, pnl: float, now: Optional[datetime] = None) -> None:
        now = now or _now()
        if pnl < 0:
            self.consecutive_losses += 1
            logger.warning("Thua lỗ liên tiếp: %d/%d",
                           self.consecutive_losses, self.config.circuit_breaker_consecutive_losses)
            if self.consecutive_losses >= self.config.circuit_breaker_consecutive_losses:
                self.cooldown_until = now + timedelta(
                    minutes=self.config.circuit_breaker_cooldown_min
                )
                logger.error(
                    "CIRCUIT BREAKER: %d lần thua liên tiếp -> cooldown đến %s",
                    self.consecutive_losses, self.cooldown_until.isoformat(),
                )
        else:
            self.consecutive_losses = 0

    def can_trade(self, now: Optional[datetime] = None, equity: Optional[float] = None) -> Tuple[bool, str]:
        now = now or _now()
        if equity is not None:
            self.update_equity(equity)

        if self.drawdown_tripped:
            return False, (
                f"max drawdown {self.config.max_drawdown_pct:.0%} đã bị vượt; "
                "cần reset thủ công"
            )

        if self.cooldown_until is not None:
            if now < self.cooldown_until:
                remaining = (self.cooldown_until - now).total_seconds() / 60
                return False, f"cooldown sau chuỗi thua, còn {remaining:.0f} phút"
            self.cooldown_until = None
            self.consecutive_losses = 0

        if self.consecutive_losses >= self.config.circuit_breaker_consecutive_losses:
            return False, f"consecutive losses = {self.consecutive_losses}"

        return True, "ok"

    def snapshot(self) -> Dict[str, Any]:
        return {
            "consecutive_losses": self.consecutive_losses,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "drawdown_tripped": self.drawdown_tripped,
            "peak_equity": self.peak_equity,
        }

    def restore(self, data: Optional[Dict[str, Any]]) -> None:
        if not data:
            return
        self.consecutive_losses = int(data.get("consecutive_losses", 0))
        cooldown = data.get("cooldown_until")
        self.cooldown_until = datetime.fromisoformat(cooldown) if cooldown else None
        self.drawdown_tripped = bool(data.get("drawdown_tripped", False))
        self.peak_equity = float(data.get("peak_equity", 0.0))

    def reset(self) -> None:
        self.consecutive_losses = 0
        self.cooldown_until = None
        self.drawdown_tripped = False
        self.peak_equity = 0.0
        logger.info("Circuit breaker đã reset")

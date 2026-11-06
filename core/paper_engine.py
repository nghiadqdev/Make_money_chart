"""Virtual ledger / paper execution engine.

Accounting model (spot, no leverage, margin reserved per position):

    free_cash  : available USDT
    margin     : notional reserved for each open position
    equity     : free_cash + sum(margin) + sum(unrealized_pnl)

Opening a position deducts ``notional + entry_fee`` from free_cash.
Closing returns ``margin + gross_pnl - exit_fee``.

Short positions are simulated by reserving margin; borrow fees/funding are
ignored (documented limitation for paper trading).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.loader import TradingConfig
from core.database import Database
from core.logging_config import get_logger
from core.types import (
    ClosedTrade,
    ExitReason,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    Position,
    Side,
    TradePlan,
)

logger = get_logger("paper")

STATE_VERSION = 1


class PaperEngineError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PaperEngine:
    def __init__(
        self,
        config: TradingConfig,
        db: Database,
        state_path: str | Path,
        symbol: str = "BTC/USDT",
    ):
        self.config = config
        self.db = db
        self.state_path = Path(state_path)
        self.symbol = symbol

        self.free_cash: float = float(config.initial_balance)
        self.realized_pnl: float = 0.0
        self.positions: Dict[str, Position] = {}
        self.last_prices: Dict[str, float] = {}
        self.peak_equity: float = float(config.initial_balance)
        self.consecutive_losses: int = 0
        self.meta: Dict[str, Any] = {}
        self.created_at: str = _now_iso()

    # ------------------------------------------------------------------ #
    # Valuation
    # ------------------------------------------------------------------ #
    def _mark(self, symbol: str, fallback: float = 0.0) -> float:
        return float(self.last_prices.get(symbol, fallback))

    def position_unrealized(self, position: Position) -> float:
        price = self._mark(position.symbol, position.last_price or position.entry_price)
        return position.unrealized_pnl(price)

    @property
    def unrealized_pnl(self) -> float:
        return sum(self.position_unrealized(p) for p in self.positions.values())

    @property
    def exposure(self) -> float:
        return sum(p.margin for p in self.positions.values())

    @property
    def equity(self) -> float:
        return self.free_cash + self.exposure + self.unrealized_pnl

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, 1.0 - self.equity / self.peak_equity)

    @property
    def tranche_size(self) -> float:
        return self.equity / max(1, self.config.tranches)

    def update_price(self, symbol: str, price: float) -> None:
        if price <= 0:
            raise PaperEngineError(f"Giá không hợp lệ: {price}")
        self.last_prices[symbol] = float(price)
        for pos in self.positions.values():
            if pos.symbol == symbol:
                pos.last_price = float(price)
        self._update_peak()

    def _update_peak(self) -> None:
        eq = self.equity
        if eq > self.peak_equity:
            self.peak_equity = eq

    def snapshot(self, circuit_open: bool = False, circuit_reason: str = "") -> PortfolioSnapshot:
        return PortfolioSnapshot(
            equity=self.equity,
            free_cash=self.free_cash,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
            exposure=self.exposure,
            open_positions=len(self.positions),
            peak_equity=self.peak_equity,
            drawdown_pct=self.drawdown_pct,
            consecutive_losses=self.consecutive_losses,
            circuit_open=circuit_open,
            circuit_reason=circuit_reason,
        )

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    def can_open(self, plan: TradePlan) -> tuple[bool, str]:
        if len(self.positions) >= self.config.max_open_positions:
            return False, f"Đã đạt max_open_positions={self.config.max_open_positions}"
        if any(p.symbol == plan.symbol for p in self.positions.values()):
            return False, f"Đã có position cho {plan.symbol}"
        if plan.notional < self.config.min_notional:
            return False, f"Notional {plan.notional:.2f} < min_notional {self.config.min_notional}"
        fee = plan.notional * self.config.taker_fee
        if self.free_cash < plan.notional + fee + 1e-9:
            return False, f"Không đủ free cash: cần {plan.notional + fee:.4f}, có {self.free_cash:.4f}"
        return True, "ok"

    def open_position(self, plan: TradePlan, opened_at: Optional[str] = None) -> Position:
        ok, reason = self.can_open(plan)
        if not ok:
            raise PaperEngineError(f"Không thể mở position: {reason}")

        entry_fee = plan.notional * self.config.taker_fee
        self.free_cash -= plan.notional + entry_fee

        pos_id = str(uuid.uuid4())
        order_ts = opened_at or _now_iso()
        entry_side = OrderSide.BUY if plan.side is Side.LONG else OrderSide.SELL
        exit_side = OrderSide.SELL if plan.side is Side.LONG else OrderSide.BUY

        entry_order = Order(
            id=str(uuid.uuid4()), position_id=pos_id, symbol=plan.symbol,
            side=entry_side, order_type=OrderType.MARKET_ENTRY, qty=plan.qty,
            requested_price=plan.entry_price, fill_price=plan.entry_price,
            status=OrderStatus.FILLED, fee=entry_fee,
            created_at=order_ts, filled_at=order_ts, reason="entry",
        )
        sl_order = Order(
            id=str(uuid.uuid4()), position_id=pos_id, symbol=plan.symbol,
            side=exit_side, order_type=OrderType.STOP_LOSS, qty=plan.qty,
            requested_price=plan.stop_loss, fill_price=None,
            status=OrderStatus.WORKING, fee=0.0,
            created_at=order_ts, reason="stop_loss",
        )
        tp_order = Order(
            id=str(uuid.uuid4()), position_id=pos_id, symbol=plan.symbol,
            side=exit_side, order_type=OrderType.TAKE_PROFIT, qty=plan.qty,
            requested_price=plan.take_profit, fill_price=None,
            status=OrderStatus.WORKING, fee=0.0,
            created_at=order_ts, reason="take_profit",
        )
        for order in (entry_order, sl_order, tp_order):
            self.db.insert_order(order)

        pos = Position(
            id=pos_id,
            symbol=plan.symbol,
            side=plan.side,
            qty=plan.qty,
            entry_price=plan.entry_price,
            margin=plan.notional,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            entry_fee=entry_fee,
            opened_at=order_ts,
            reason=plan.reason,
            last_price=plan.entry_price,
            entry_order_id=entry_order.id,
            sl_order_id=sl_order.id,
            tp_order_id=tp_order.id,
        )
        self.positions[pos.id] = pos
        self.last_prices.setdefault(pos.symbol, pos.entry_price)
        self._update_peak()

        logger.info(
            "SIM ORDER FILLED %s %s qty=%.8f @ %.4f | notional=%.4f fee=%.6f",
            entry_side.value.upper(), pos.symbol, pos.qty, pos.entry_price,
            pos.margin, pos.entry_fee,
        )
        logger.info(
            "SIM ORDERS WORKING %s | SL %.4f | TP %.4f",
            pos.symbol, pos.stop_loss, pos.take_profit,
        )
        self.db.insert_event(
            "INFO", "position_opened",
            {"position_id": pos.id, "symbol": pos.symbol, "side": pos.side.value,
             "qty": pos.qty, "entry": pos.entry_price, "sl": pos.stop_loss,
             "tp": pos.take_profit, "fee": pos.entry_fee, "reason": pos.reason},
        )
        self.save_state()
        return pos

    def _cancel_order(self, order_id: str, reason: str) -> None:
        if not order_id:
            return
        self.db.update_order(order_id, OrderStatus.CANCELLED, reason=reason)
        logger.info("SIM ORDER CANCELLED %s (%s)", order_id, reason)

    def _settle_exit_orders(
        self,
        pos: Position,
        price: float,
        reason: str,
        fee: float,
        closed_at: str,
    ) -> None:
        """Giả lập khớp lệnh thoát: SL/TP working hoặc market exit."""
        exit_side = OrderSide.SELL if pos.side is Side.LONG else OrderSide.BUY
        handled = False

        if reason == ExitReason.STOP_LOSS.value and pos.sl_order_id:
            self.db.update_order(
                pos.sl_order_id, OrderStatus.FILLED, fill_price=price,
                fee=fee, filled_at=closed_at, reason="stop_loss filled",
            )
            self._cancel_order(pos.tp_order_id, "SL filled")
            handled = True
            logger.info("SIM STOP-LOSS FILLED %s %s @ %.4f", exit_side.value.upper(), pos.symbol, price)
        elif reason == ExitReason.TAKE_PROFIT.value and pos.tp_order_id:
            self.db.update_order(
                pos.tp_order_id, OrderStatus.FILLED, fill_price=price,
                fee=fee, filled_at=closed_at, reason="take_profit filled",
            )
            self._cancel_order(pos.sl_order_id, "TP filled")
            handled = True
            logger.info("SIM TAKE-PROFIT FILLED %s %s @ %.4f", exit_side.value.upper(), pos.symbol, price)

        if not handled:
            self._cancel_order(pos.sl_order_id, f"exit {reason}")
            self._cancel_order(pos.tp_order_id, f"exit {reason}")
            exit_order = Order(
                id=str(uuid.uuid4()), position_id=pos.id, symbol=pos.symbol,
                side=exit_side, order_type=OrderType.MARKET_EXIT, qty=pos.qty,
                requested_price=float(price), fill_price=float(price),
                status=OrderStatus.FILLED, fee=fee,
                created_at=closed_at, filled_at=closed_at, reason=reason,
            )
            self.db.insert_order(exit_order)
            logger.info(
                "SIM MARKET EXIT FILLED %s %s qty=%.8f @ %.4f fee=%.6f (%s)",
                exit_side.value.upper(), pos.symbol, pos.qty, price, fee, reason,
            )

    def close_position(
        self,
        position_id: str,
        price: float,
        reason: ExitReason | str,
        closed_at: Optional[str] = None,
    ) -> ClosedTrade:
        pos = self.positions.get(position_id)
        if pos is None:
            raise PaperEngineError(f"Position không tồn tại: {position_id}")
        if price <= 0:
            raise PaperEngineError(f"Giá đóng không hợp lệ: {price}")

        reason_value = reason.value if isinstance(reason, ExitReason) else str(reason)
        close_ts = closed_at or _now_iso()
        gross_pnl = (price - pos.entry_price) * pos.qty * pos.direction
        exit_fee = price * pos.qty * self.config.taker_fee
        pnl = gross_pnl - pos.entry_fee - exit_fee
        return_pct = pnl / pos.margin if pos.margin else 0.0

        self._settle_exit_orders(pos, float(price), reason_value, exit_fee, close_ts)

        self.free_cash += pos.margin + gross_pnl - exit_fee
        self.realized_pnl += pnl
        del self.positions[position_id]

        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        self._update_peak()

        trade = ClosedTrade(
            id=pos.id,
            symbol=pos.symbol,
            side=pos.side,
            qty=pos.qty,
            entry_price=pos.entry_price,
            exit_price=float(price),
            sl=pos.stop_loss,
            tp=pos.take_profit,
            margin=pos.margin,
            entry_fee=pos.entry_fee,
            exit_fee=exit_fee,
            gross_pnl=gross_pnl,
            pnl=pnl,
            return_pct=return_pct,
            opened_at=pos.opened_at,
            closed_at=close_ts,
            reason=reason_value,
        )
        self.db.insert_trade(trade)
        logger.info(
            "CLOSE %s %s exit=%.4f pnl=%+.6f (%.2f%%) reason=%s",
            pos.side.value.upper(), pos.symbol, price, pnl, return_pct * 100, reason_value,
        )
        self.save_state()
        return trade

    def evaluate_exits(
        self,
        symbol: str,
        candle_open: float,
        candle_high: float,
        candle_low: float,
        candle_time: Optional[str] = None,
    ) -> List[ClosedTrade]:
        """Check SL/TP against the last closed candle. SL is checked first."""
        closed: List[ClosedTrade] = []
        for pos in list(self.positions.values()):
            if pos.symbol != symbol:
                continue
            exit_price: Optional[float] = None
            exit_reason: Optional[ExitReason] = None

            if pos.side is Side.LONG:
                if candle_open <= pos.stop_loss or candle_low <= pos.stop_loss:
                    exit_price = candle_open if candle_open <= pos.stop_loss else pos.stop_loss
                    exit_reason = ExitReason.STOP_LOSS
                elif candle_open >= pos.take_profit or candle_high >= pos.take_profit:
                    exit_price = candle_open if candle_open >= pos.take_profit else pos.take_profit
                    exit_reason = ExitReason.TAKE_PROFIT
            else:  # SHORT
                if candle_open >= pos.stop_loss or candle_high >= pos.stop_loss:
                    exit_price = candle_open if candle_open >= pos.stop_loss else pos.stop_loss
                    exit_reason = ExitReason.STOP_LOSS
                elif candle_open <= pos.take_profit or candle_low <= pos.take_profit:
                    exit_price = candle_open if candle_open <= pos.take_profit else pos.take_profit
                    exit_reason = ExitReason.TAKE_PROFIT

            if exit_price is not None and exit_reason is not None:
                closed.append(self.close_position(pos.id, exit_price, exit_reason, candle_time))
        return closed

    def force_close_all(
        self,
        price: float,
        reason: ExitReason = ExitReason.SHUTDOWN,
        closed_at: Optional[str] = None,
    ) -> List[ClosedTrade]:
        trades = [
            self.close_position(pos.id, price, reason, closed_at)
            for pos in list(self.positions.values())
        ]
        return trades

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def _state_payload(self) -> Dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "created_at": self.created_at,
            "free_cash": self.free_cash,
            "realized_pnl": self.realized_pnl,
            "peak_equity": self.peak_equity,
            "consecutive_losses": self.consecutive_losses,
            "last_prices": self.last_prices,
            "positions": [p.to_dict() for p in self.positions.values()],
            "meta": self.meta,
        }

    def save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self._state_payload(), fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    def load_state(self) -> bool:
        if not self.state_path.exists():
            return False
        try:
            with self.state_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Không đọc được state %s: %s", self.state_path, exc)
            return False

        if data.get("version") != STATE_VERSION:
            raise PaperEngineError(
                f"State version {data.get('version')} không tương thích (cần {STATE_VERSION})"
            )

        self.created_at = data.get("created_at", self.created_at)
        self.free_cash = float(data.get("free_cash", self.config.initial_balance))
        self.realized_pnl = float(data.get("realized_pnl", 0.0))
        self.peak_equity = float(data.get("peak_equity", self.free_cash))
        self.consecutive_losses = int(data.get("consecutive_losses", 0))
        self.last_prices = {str(k): float(v) for k, v in (data.get("last_prices") or {}).items()}
        self.meta = data.get("meta") or {}
        self.positions = {
            p["id"]: Position.from_dict(p) for p in data.get("positions", [])
        }
        logger.info(
            "Đã khôi phục state: equity=%.4f, free=%.4f, positions=%d",
            self.equity, self.free_cash, len(self.positions),
        )
        return True

    def reset(self) -> None:
        self.free_cash = float(self.config.initial_balance)
        self.realized_pnl = 0.0
        self.positions.clear()
        self.last_prices.clear()
        self.peak_equity = float(self.config.initial_balance)
        self.consecutive_losses = 0
        self.meta = {}
        self.created_at = _now_iso()
        if self.state_path.exists():
            self.state_path.unlink()
        self.db.insert_event("INFO", "paper_engine_reset", {"initial_balance": self.free_cash})
        logger.info("Đã reset paper engine về %.2f USDT", self.free_cash)

    def set_meta(self, key: str, value: Any) -> None:
        self.meta[key] = value

    def get_meta(self, key: str, default: Any = None) -> Any:
        return self.meta.get(key, default)

"""Shared domain types for the paper-trading bot."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def direction(self) -> int:
        return 1 if self is Side.LONG else -1


class SignalType(str, Enum):
    LONG = "long"
    SHORT = "short"
    HOLD = "hold"


class ExitReason(str, Enum):
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    MANUAL = "manual"
    SHUTDOWN = "shutdown"
    FORCE_CLOSE = "force_close"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET_ENTRY = "market_entry"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    MARKET_EXIT = "market_exit"


class OrderStatus(str, Enum):
    WORKING = "working"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class BotState(str, Enum):
    START = "start"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass(frozen=True)
class ModelPrediction:
    p_up: float
    p_down: float
    expected_return: float
    score: float
    confidence: float
    last_price: float
    path_returns: List[float] = field(default_factory=list)
    quantiles: Dict[str, float] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TradeSignal:
    symbol: str
    signal: SignalType
    price: float
    atr: float
    score: float
    confidence: float
    p_up: float
    p_down: float
    expected_return: float
    reasons: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=utc_now_iso)

    @property
    def is_actionable(self) -> bool:
        return self.signal is not SignalType.HOLD

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["signal"] = self.signal.value
        return data


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    side: Side
    entry_price: float
    qty: float
    notional: float
    stop_loss: float
    take_profit: float
    stop_distance: float
    risk_amount: float
    r_multiple: float
    reason: str
    signal: Optional[TradeSignal] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["side"] = self.side.value
        if self.signal is not None:
            data["signal"] = self.signal.to_dict()
        return data


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    plan: Optional[TradePlan] = None
    checks: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    id: str
    symbol: str
    side: Side
    qty: float
    entry_price: float
    margin: float
    stop_loss: float
    take_profit: float
    entry_fee: float
    opened_at: str
    reason: str = ""
    last_price: float = 0.0
    entry_order_id: str = ""
    sl_order_id: str = ""
    tp_order_id: str = ""

    @property
    def direction(self) -> int:
        return self.side.direction

    def unrealized_pnl(self, price: Optional[float] = None) -> float:
        mark = float(price if price is not None else self.last_price)
        return (mark - self.entry_price) * self.qty * self.direction

    def notional_at(self, price: float) -> float:
        return self.qty * float(price)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["side"] = self.side.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Position":
        payload = dict(data)
        payload["side"] = Side(payload["side"])
        return cls(**payload)


@dataclass
class Order:
    id: str
    position_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: float
    requested_price: float
    fill_price: Optional[float]
    status: OrderStatus
    fee: float = 0.0
    created_at: str = field(default_factory=utc_now_iso)
    filled_at: Optional[str] = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["side"] = self.side.value
        data["order_type"] = self.order_type.value
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class ClosedTrade:
    id: str
    symbol: str
    side: Side
    qty: float
    entry_price: float
    exit_price: float
    sl: float
    tp: float
    margin: float
    entry_fee: float
    exit_fee: float
    gross_pnl: float
    pnl: float
    return_pct: float
    opened_at: str
    closed_at: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["side"] = self.side.value
        return data


@dataclass(frozen=True)
class PortfolioSnapshot:
    equity: float
    free_cash: float
    realized_pnl: float
    unrealized_pnl: float
    exposure: float
    open_positions: int
    peak_equity: float
    drawdown_pct: float
    consecutive_losses: int
    circuit_open: bool
    circuit_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

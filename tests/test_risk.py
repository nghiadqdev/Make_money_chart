from datetime import datetime, timedelta, timezone

import pytest

from config.loader import TradingConfig
from core.types import PortfolioSnapshot, Side, SignalType, TradeSignal
from risk_management.circuit_breaker import CircuitBreaker
from risk_management.manager import RiskManager
from risk_management.position_sizer import PositionSizer


def cfg(**overrides):
    base = dict(
        initial_balance=100.0,
        taker_fee=0.001,
        tranches=4,
        max_open_positions=4,
        risk_per_trade_pct=0.01,
        sl_atr_mult=1.5,
        tp_atr_mult=2.5,
        min_sl_pct=0.006,
        max_sl_pct=0.03,
        min_notional=5.0,
        circuit_breaker_consecutive_losses=3,
        circuit_breaker_cooldown_min=120,
        max_drawdown_pct=0.15,
    )
    base.update(overrides)
    return TradingConfig(**base)


def snapshot(**overrides):
    base = dict(
        equity=100.0,
        free_cash=100.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        exposure=0.0,
        open_positions=0,
        peak_equity=100.0,
        drawdown_pct=0.0,
        consecutive_losses=0,
        circuit_open=False,
    )
    base.update(overrides)
    return PortfolioSnapshot(**base)


def signal(sig=SignalType.LONG, price=100.0, atr=2.0):
    return TradeSignal(
        symbol="BTC/USDT",
        signal=sig,
        price=price,
        atr=atr,
        score=0.3,
        confidence=0.7,
        p_up=0.7,
        p_down=0.3,
        expected_return=0.02,
    )


def test_sizer_never_exceeds_one_tranche():
    sizer = PositionSizer(cfg())
    plan, reason = sizer.build_plan(
        side=Side.LONG,
        equity=100.0,
        free_cash=100.0,
        entry_price=100.0,
        atr=2.0,
        symbol="BTC/USDT",
        signal=signal(),
    )
    assert reason == "ok"
    assert plan is not None
    assert plan.notional <= 25.0 + 1e-9
    assert plan.qty == pytest.approx(plan.notional / 100.0)
    assert plan.stop_loss == pytest.approx(97.0)  # 1.5 * ATR = 3, clamped to 3%
    assert plan.take_profit == pytest.approx(105.0)  # 2.5 * ATR
    assert plan.risk_amount == pytest.approx(1.0)


def test_sizer_respects_min_stop_and_max_stop():
    sizer = PositionSizer(cfg(sl_atr_mult=0.1, max_sl_pct=0.02))
    plan, _ = sizer.build_plan(
        side=Side.LONG,
        equity=100.0, free_cash=100.0, entry_price=100.0, atr=2.0,
        symbol="BTC/USDT",
    )
    # 0.1 * 2 = 0.2, but min_sl_pct 0.6% => 0.6
    assert plan.stop_loss == pytest.approx(99.4)


def test_circuit_breaker_after_three_losses():
    cb = CircuitBreaker(cfg())
    now = datetime.now(timezone.utc)
    for _ in range(3):
        cb.record_trade(-1.0, now=now)
    ok, reason = cb.can_trade(now=now, equity=97.0)
    assert not ok
    assert "cooldown" in reason
    # After cooldown expires it should allow trading again.
    ok2, _ = cb.can_trade(now=now + timedelta(minutes=121), equity=97.0)
    assert ok2


def test_circuit_breaker_max_drawdown_requires_manual_reset():
    cb = CircuitBreaker(cfg(max_drawdown_pct=0.10))
    cb.update_equity(100.0)
    cb.update_equity(85.0)
    ok, reason = cb.can_trade(equity=85.0)
    assert not ok
    assert "drawdown" in reason
    cb.reset()
    assert cb.can_trade(equity=85.0)[0]


def test_risk_manager_rejects_duplicate_symbol_and_max_positions():
    rm = RiskManager(cfg())
    decision = rm.evaluate(signal(), snapshot(open_positions=4))
    assert not decision.approved
    assert "max_open_positions" in decision.reason

    decision2 = rm.evaluate(signal(), snapshot(open_positions=1), open_symbols={"BTC/USDT"})
    assert not decision2.approved
    assert "position" in decision2.reason


def test_risk_manager_approves_valid_long():
    rm = RiskManager(cfg())
    decision = rm.evaluate(signal(), snapshot())
    assert decision.approved
    assert decision.plan is not None
    assert decision.plan.stop_loss < decision.plan.entry_price < decision.plan.take_profit

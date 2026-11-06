import pytest

from config.loader import TradingConfig
from core.database import Database
from core.paper_engine import PaperEngine
from core.types import ExitReason, Side, TradePlan


def make_engine(tmp_path):
    cfg = TradingConfig(initial_balance=100.0, taker_fee=0.001, tranches=4)
    db = Database(tmp_path / "test.db")
    return PaperEngine(cfg, db, tmp_path / "state.json", symbol="BTC/USDT"), db


def make_plan(side=Side.LONG, entry=100.0, qty=0.25, sl=95.0, tp=110.0):
    return TradePlan(
        symbol="BTC/USDT",
        side=side,
        entry_price=entry,
        qty=qty,
        notional=qty * entry,
        stop_loss=sl,
        take_profit=tp,
        stop_distance=abs(entry - sl),
        risk_amount=1.0,
        r_multiple=2.0,
        reason="test",
    )


def test_long_open_close_fees_and_pnl(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.open_position(make_plan(), opened_at="2026-01-01T00:00:00+00:00")

    assert engine.free_cash == pytest.approx(74.975)
    assert engine.equity == pytest.approx(99.975)
    assert len(engine.positions) == 1
    orders = db.recent_orders()
    assert len(orders) == 3
    assert sum(1 for o in orders if o["status"] == "filled") == 1
    assert sum(1 for o in orders if o["status"] == "working") == 2

    engine.update_price("BTC/USDT", 110.0)
    assert engine.unrealized_pnl == pytest.approx(2.5)
    assert engine.equity == pytest.approx(102.475)

    trade = engine.close_position(
        next(iter(engine.positions)), 110.0, ExitReason.TAKE_PROFIT
    )
    assert trade.gross_pnl == pytest.approx(2.5)
    assert trade.pnl == pytest.approx(2.5 - 0.025 - 0.0275)
    assert engine.realized_pnl == pytest.approx(trade.pnl)

    orders = db.recent_orders()
    exit_orders = [o for o in orders if o["order_type"] != "market_entry"]
    assert any(o["order_type"] == "take_profit" and o["status"] == "filled" for o in exit_orders)
    assert any(o["order_type"] == "stop_loss" and o["status"] == "cancelled" for o in exit_orders)
    assert engine.free_cash == pytest.approx(100.0 + trade.pnl)
    assert not engine.positions

    stats = db.stats()
    assert stats["trades"] == 1
    assert stats["wins"] == 1
    db.close()


def test_long_stop_loss_is_checked_before_take_profit(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.open_position(make_plan(sl=95.0, tp=110.0))
    engine.update_price("BTC/USDT", 100.0)

    closed = engine.evaluate_exits("BTC/USDT", candle_open=99.0, candle_high=111.0, candle_low=94.0)
    assert len(closed) == 1
    assert closed[0].reason == ExitReason.STOP_LOSS.value
    assert closed[0].exit_price == pytest.approx(95.0)
    assert closed[0].pnl < 0
    assert engine.consecutive_losses == 1
    orders = db.recent_orders()
    assert any(o["order_type"] == "stop_loss" and o["status"] == "filled" for o in orders)
    assert any(o["order_type"] == "take_profit" and o["status"] == "cancelled" for o in orders)
    db.close()


def test_short_take_profit(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.open_position(make_plan(side=Side.SHORT, sl=105.0, tp=90.0))
    engine.update_price("BTC/USDT", 95.0)

    closed = engine.evaluate_exits("BTC/USDT", candle_open=96.0, candle_high=101.0, candle_low=89.0)
    assert len(closed) == 1
    assert closed[0].reason == ExitReason.TAKE_PROFIT.value
    assert closed[0].exit_price == pytest.approx(90.0)
    assert closed[0].pnl > 0
    db.close()


def test_gap_fill_uses_candle_open(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.open_position(make_plan(sl=95.0, tp=110.0))
    # Gap down through SL: fill at open (worse than SL) to avoid optimistic fills.
    closed = engine.evaluate_exits("BTC/USDT", candle_open=90.0, candle_high=96.0, candle_low=89.0)
    assert closed[0].exit_price == pytest.approx(90.0)
    db.close()


def test_force_close_cancels_working_orders_and_creates_market_exit(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.open_position(make_plan())
    trades = engine.force_close_all(101.0)
    assert len(trades) == 1

    orders = db.recent_orders()
    assert any(o["order_type"] == "stop_loss" and o["status"] == "cancelled" for o in orders)
    assert any(o["order_type"] == "take_profit" and o["status"] == "cancelled" for o in orders)
    exit_orders = [o for o in orders if o["order_type"] == "market_exit"]
    assert len(exit_orders) == 1
    assert exit_orders[0]["status"] == "filled"
    assert exit_orders[0]["fee"] > 0
    db.close()


def test_state_persistence(tmp_path):
    engine, db = make_engine(tmp_path)
    engine.open_position(make_plan())
    engine.update_price("BTC/USDT", 105.0)
    engine.save_state()

    restored, db2 = make_engine(tmp_path)
    assert restored.load_state() is True
    assert len(restored.positions) == 1
    assert restored.free_cash == pytest.approx(engine.free_cash)
    assert restored.equity == pytest.approx(engine.equity)
    db.close()
    db2.close()

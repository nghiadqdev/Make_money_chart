#!/usr/bin/env python3
"""Kronos × KuCoin paper-trading bot.

Commands
--------
    python main.py run --config config/settings.yaml
    python main.py run --once
    python main.py status
    python main.py stop          # gửi tín hiệu Stop cho bot đang chạy
    python main.py start         # xoá cờ Stop
    python main.py reset --yes

The engine is paper-only. No private order endpoint is called and no real
funds are at risk.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd

from config.loader import Settings, load_settings
from core.calibration import CalibrationManager
from core.database import Database
from core.exchange import ExchangeError, KuCoinExchange
from core.forecast_tracker import ForecastTracker
from core.logging_config import get_logger, setup_logging
from core.paper_engine import PaperEngine, PaperEngineError
from core.state_machine import ControlPlane, StateMachine
from core.types import BotState, ExitReason, SignalType
from models.kronos_signal import KronosSignalModel, ModelInferenceError
from risk_management.manager import RiskManager
from strategies.kronos_trend import KronosTrendStrategy

logger = get_logger("main")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _print_json(data: Dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _close_all_now(engine, risk, price: float, db: Database, machine, control) -> None:
    """Đóng toàn bộ position tại giá hiện tại và lưu state/risk."""
    if not engine.positions:
        return
    trades = engine.force_close_all(price, ExitReason.FORCE_CLOSE)
    for trade in trades:
        risk.on_trade_closed(trade)
    engine.set_meta("risk", risk.snapshot())
    engine.save_state()
    db.insert_event(
        "WARNING", "force_close_all",
        {"price": price, "closed": len(trades),
         "trades": [t.to_dict() for t in trades]},
    )


def _sleep_with_control(seconds: int, control: ControlPlane, machine: StateMachine, force_stop: dict) -> None:
    """Sleep theo từng giây để phản hồi Stop/Force-close nhanh."""
    deadline = time.time() + max(0, seconds)
    while time.time() < deadline:
        if force_stop.get("flag"):
            return
        state = control.read()
        if state == "force_close":
            force_stop["flag"] = True
            machine.request_stop("control force_close")
            return
        if state == "stop" and machine.allow_new_entries:
            machine.request_stop("control stop")
        time.sleep(min(1.0, max(0.0, deadline - time.time())))


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    setup_logging(settings.storage.log_dir, settings.storage.log_level)
    logger.info("=" * 70)
    logger.info("Kronos KuCoin PAPER bot | symbol=%s timeframe=%s",
                settings.market.symbol, settings.market.timeframe)
    logger.info("Initial balance: %.2f USDT | fee=%.3f%% | tranches=%d",
                settings.trading.initial_balance, settings.trading.taker_fee * 100,
                settings.trading.tranches)
    logger.info("=" * 70)

    db = Database(settings.storage.db_path)
    engine = PaperEngine(
        settings.trading,
        db,
        settings.storage.state_path,
        symbol=settings.market.symbol,
    )
    if engine.load_state():
        logger.info("Restored paper state: equity=%.4f positions=%d",
                    engine.equity, len(engine.positions))
    else:
        logger.info("New paper ledger: %.2f USDT", engine.free_cash)

    risk = RiskManager(settings.trading)
    risk.restore(engine.get_meta("risk"))
    risk.on_equity(engine.equity)

    control = ControlPlane(settings.storage.control_path)
    machine = StateMachine(
        BotState.STOPPING if control.read() == "stop" else BotState.START
    )
    if machine.is_stopping:
        logger.warning("Control file đang ở trạng thái STOP; bot sẽ chỉ quản lý position cũ.")

    exchange = KuCoinExchange(settings.exchange)
    try:
        exchange.connect()
        exchange.validate_symbol(settings.market.symbol)
        logger.info("Market metadata: %s", exchange.market_metadata(settings.market.symbol))
    except ExchangeError as exc:
        logger.error("Không kết nối được KuCoin: %s", exc)
        db.insert_event("ERROR", "exchange_connect_failed", {"error": str(exc)})
        db.close()
        return 2

    strategy = None
    model = None
    if machine.allow_new_entries:
        strategy = KronosTrendStrategy(
            settings.model, settings.strategy, symbol=settings.market.symbol
        )
        try:
            model = KronosSignalModel(settings.model, timeframe=settings.market.timeframe)
        except Exception as exc:
            logger.exception("Không load được Kronos: %s", exc)
            db.insert_event("ERROR", "model_load_failed", {"error": str(exc)})
            exchange.close()
            db.close()
            return 2
    else:
        logger.warning("State=STOPPING: bỏ qua load Kronos, chỉ quản lý position cũ.")

    calibration = CalibrationManager(settings.learning, settings.model)
    tracker = ForecastTracker(db, settings.model, settings.model.fee_buffer_pct)
    if strategy is not None:
        strategy.set_thresholds(calibration.long_threshold, calibration.short_threshold)
        logger.info("Thresholds | long=%.3f short=%.3f", calibration.long_threshold, calibration.short_threshold)

    # --- graceful shutdown ------------------------------------------------ #
    stop_signals = {"count": 0}
    force_stop = {"flag": False}

    def _handle_signal(signum, _frame):
        stop_signals["count"] += 1
        if stop_signals["count"] == 1:
            logger.warning("Nhận signal %s -> STOPPING (không mở lệnh mới)", signum)
            machine.request_stop(f"signal {signum}")
            control.request_stop()
        else:
            logger.error("Nhận signal %s lần 2 -> force close", signum)
            force_stop["flag"] = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    last_processed_candle = engine.get_meta("last_processed_candle")
    last_exit_candle = engine.get_meta("last_exit_candle")
    current_lookback = int(engine.get_meta("adaptive_lookback", settings.model.lookback))
    current_lookback = max(50, min(current_lookback, settings.model.lookback_max, settings.model.max_context))
    logger.info("Adaptive lookback bắt đầu từ %d (max=%d)", current_lookback, settings.model.lookback_max)
    stop_requested_at: Optional[float] = None
    cycles = 0

    while True:
        cycles += 1
        try:
            # Control file có thể đổi bất cứ lúc nào (dashboard).
            ctl = control.read()
            if ctl == "force_close":
                force_stop["flag"] = True
                machine.request_stop("control force_close")
            elif ctl == "stop" and machine.allow_new_entries:
                machine.request_stop("control stop")

            df = exchange.fetch_ohlcv(
                settings.market.symbol,
                settings.market.timeframe,
                limit=settings.market.ohlcv_limit,
            )
            min_rows = max(current_lookback + 2, settings.strategy.ema_slow + 2)
            if len(df) < min_rows:
                raise ExchangeError(
                    f"Chỉ có {len(df)} nến, cần ít nhất {min_rows} để chạy an toàn"
                )

            closed = df.iloc[:-1] if settings.market.closed_candle_only and len(df) > 1 else df
            last_closed = closed.iloc[-1]
            last_closed_ts = pd.Timestamp(last_closed["timestamp"]).isoformat()
            mark_price = float(df["close"].iloc[-1])

            engine.update_price(settings.market.symbol, mark_price)
            db.upsert_candles(df, settings.market.symbol, settings.market.timeframe)

            # Đánh giá các forecast đã đến hạn + hiệu chỉnh ngưỡng (nếu đủ mẫu).
            if settings.learning.enabled:
                tracker.evaluate_pending(settings.market.symbol, settings.market.timeframe)
                calib = calibration.maybe_update(db)
                if strategy is not None:
                    strategy.set_thresholds(calib["long_threshold"], calib["short_threshold"])

            # Force-close ngay khi dashboard yêu cầu.
            if force_stop["flag"]:
                logger.warning("Force-close theo yêu cầu dashboard tại %.4f", mark_price)
                _close_all_now(engine, risk, mark_price, db, machine, control)
                machine.mark_stopped("force_closed")
                control.clear()
                break

            # 1) Xử lý SL/TP trên mọi nến đã đóng mà bot chưa xử lý.
            if last_exit_candle is None:
                candles_to_check = closed.tail(1)
            else:
                candles_to_check = closed[closed["timestamp"] > pd.Timestamp(last_exit_candle)]
            for _, candle in candles_to_check.iterrows():
                closed_trades = engine.evaluate_exits(
                    settings.market.symbol,
                    float(candle["open"]),
                    float(candle["high"]),
                    float(candle["low"]),
                    str(candle["timestamp"]),
                )
                for trade in closed_trades:
                    risk.on_trade_closed(trade)
                    logger.info("Trade closed | pnl=%+.4f | reason=%s", trade.pnl, trade.reason)
            last_exit_candle = last_closed_ts
            engine.set_meta("last_exit_candle", last_exit_candle)

            # 2) Đọc control file mỗi vòng.
            if control.read() == "stop" and machine.allow_new_entries:
                logger.warning("Control file yêu cầu STOP.")
                machine.request_stop("control file")
            if machine.is_stopping and stop_requested_at is None:
                stop_requested_at = time.time()

            # 3) Sinh signal chỉ khi có nến đóng mới và state=START.
            if (
                machine.allow_new_entries
                and model is not None
                and strategy is not None
                and last_closed_ts != last_processed_candle
            ):
                prediction = model.infer(closed, lookback=current_lookback)

                # Dashboard có thể yêu cầu force-close trong lúc inference.
                if force_stop["flag"] or control.read() == "force_close":
                    force_stop["flag"] = True
                    logger.warning("Force-close sau inference tại %.4f", mark_price)
                    _close_all_now(engine, risk, mark_price, db, machine, control)
                    machine.mark_stopped("force_closed")
                    control.clear()
                    break

                if control.read() == "stop":
                    machine.request_stop("control stop during inference")

                if not machine.allow_new_entries:
                    logger.info("Stop được yêu cầu trong lúc inference; bỏ qua signal mới.")
                else:
                    raw_signal = strategy.generate(closed, prediction)
                    # Fill tại giá hiện tại thay vì close của nến trước.
                    trade_signal = replace(raw_signal, price=mark_price)

                    snapshot = engine.snapshot()
                    decision = risk.evaluate(
                        trade_signal,
                        snapshot,
                        open_symbols=[p.symbol for p in engine.positions.values()],
                    )
                    db.insert_signal(
                        trade_signal,
                        "approved" if decision.approved else "rejected",
                        decision.reason,
                    )
                    try:
                        tracker.record_forecast(
                            closed, prediction, trade_signal,
                            "approved" if decision.approved else "rejected",
                            current_lookback, settings.market.timeframe,
                            strategy.long_threshold, strategy.short_threshold,
                            decision_reason=decision.reason,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Không lưu được forecast: %s", exc)
                    logger.info("Signal %s | %s", trade_signal.signal.value, decision.reason)

                    # Mở rộng lookback có kiểm soát: 400 -> 401 -> ... -> 512.
                    growth = settings.model.lookback_growth
                    if growth > 0:
                        current_lookback = min(
                            settings.model.lookback_max,
                            settings.model.max_context,
                            current_lookback + growth,
                        )
                        engine.set_meta("adaptive_lookback", current_lookback)
                        logger.info("Adaptive lookback -> %d", current_lookback)

                    if decision.approved and decision.plan is not None:
                        try:
                            engine.open_position(decision.plan, opened_at=last_closed_ts)
                        except PaperEngineError as exc:
                            logger.error("Không mở được position: %s", exc)
                            db.insert_event("ERROR", "open_position_failed", {"error": str(exc)})

                    last_processed_candle = last_closed_ts
                    engine.set_meta("last_processed_candle", last_processed_candle)

            # 4) Mark-to-market + snapshot.
            risk.on_equity(engine.equity)
            can_trade, breaker_reason = risk.breaker.can_trade(None, engine.equity)
            snapshot = engine.snapshot(
                circuit_open=not can_trade,
                circuit_reason=breaker_reason,
            )
            db.insert_equity(_now_iso(), snapshot)
            engine.set_meta("risk", risk.snapshot())
            engine.save_state()

            # 5) Thoát an toàn: chỉ khi không còn position.
            if machine.is_stopping:
                if not engine.positions:
                    machine.mark_stopped("flat")
                    control.clear()
                    break
                elapsed = (time.time() - stop_requested_at) if stop_requested_at else 0.0
                if force_stop["flag"] or (
                    settings.trading.shutdown_force_close_sec > 0
                    and elapsed >= settings.trading.shutdown_force_close_sec
                ):
                    logger.warning("Force-close %d position còn lại tại %.4f",
                                   len(engine.positions), mark_price)
                    _close_all_now(engine, risk, mark_price, db, machine, control)
                    machine.mark_stopped("force_closed")
                    control.clear()
                    break

            if args.once or (args.max_cycles and cycles >= args.max_cycles):
                logger.info("Đã chạy %d cycle -> thoát (không phải Stop state).", cycles)
                break

            _sleep_with_control(
                settings.market.poll_interval_sec, control, machine, force_stop
            )

        except (KeyboardInterrupt, SystemExit):
            machine.request_stop("keyboard interrupt")
            if not engine.positions:
                break
        except (ExchangeError, ModelInferenceError) as exc:
            logger.error("Lỗi vận hành: %s", exc)
            db.insert_event("ERROR", "runtime_error", {"error": str(exc)})
            try:
                exchange.reconnect()
            except Exception as reconnect_exc:
                logger.error("Reconnect thất bại: %s", reconnect_exc)
                time.sleep(settings.exchange.backoff_base_sec * 2)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Lỗi không mong đợi: %s", exc)
            db.insert_event("ERROR", "unexpected_error", {"error": str(exc)})
            time.sleep(settings.exchange.backoff_base_sec)

    final = engine.snapshot()
    logger.info(
        "Bot dừng | equity=%.4f free=%.4f realized=%+.4f open=%d",
        final.equity, final.free_cash, final.realized_pnl, final.open_positions,
    )
    db.insert_event("INFO", "bot_stopped", final.to_dict())
    exchange.close()
    db.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    db = Database(settings.storage.db_path)
    engine = PaperEngine(
        settings.trading, db, settings.storage.state_path, symbol=settings.market.symbol
    )
    engine.load_state()
    risk = RiskManager(settings.trading)
    risk.restore(engine.get_meta("risk"))
    can_trade, reason = risk.breaker.can_trade(None, engine.equity)
    snap = engine.snapshot(circuit_open=not can_trade, circuit_reason=reason)
    _print_json(
        {
            "symbol": settings.market.symbol,
            "timeframe": settings.market.timeframe,
            "control": ControlPlane(settings.storage.control_path).read(),
            "portfolio": snap.to_dict(),
            "positions": [p.to_dict() for p in engine.positions.values()],
            "risk": risk.circuit_status(),
            "stats": db.stats(),
            "recent_trades": db.recent_trades(10),
            "equity_curve_tail": db.equity_curve(20),
        }
    )
    db.close()
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    ControlPlane(settings.storage.control_path).request_stop()
    print("Đã gửi STOP. Bot đang chạy sẽ không mở lệnh mới và chờ SL/TP.")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    ControlPlane(settings.storage.control_path).request_start()
    print("Đã xoá cờ STOP. Lần chạy tiếp theo sẽ ở trạng thái START.")
    return 0


def cmd_reset_learning(args: argparse.Namespace) -> int:
    """Xoá forecast + calibration, giữ candles/trades."""
    if not args.yes:
        print("Từ chối: thêm --yes để xoá lịch sử forecast/calibration.")
        return 1
    settings = load_settings(args.config)
    db = Database(settings.storage.db_path)
    try:
        db.clear_forecasts()
        calibration = CalibrationManager(settings.learning, settings.model)
        calibration.reset()
        db.insert_event("INFO", "reset_learning", {"pred_len": settings.model.pred_len,
                                                    "lookback": settings.model.lookback})
        print("Đã xoá forecast + calibration. Giữ nguyên candles/trades/orders.")
        return 0
    finally:
        db.close()


def cmd_reset(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Từ chối reset: thêm --yes để xác nhận xoá state/ledger runtime.")
        return 1
    settings = load_settings(args.config)
    db = Database(settings.storage.db_path)
    engine = PaperEngine(
        settings.trading, db, settings.storage.state_path, symbol=settings.market.symbol
    )
    db.reset_runtime()
    engine.reset()
    RiskManager(settings.trading).reset()
    ControlPlane(settings.storage.control_path).clear()
    db.insert_event("INFO", "manual_reset", {})
    db.close()
    print(f"Đã reset paper balance về {settings.trading.initial_balance:.2f} USDT.")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kronos × KuCoin paper-trading bot")
    parser.add_argument("--config", default="config/settings.yaml", help="Đường dẫn YAML config")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Chạy bot paper trading")
    run.add_argument("--once", action="store_true", help="Chạy đúng một vòng rồi thoát")
    run.add_argument("--max-cycles", type=int, default=0, help="Giới hạn số vòng lặp (0 = vô hạn)")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="In trạng thái ledger/risk")
    status.set_defaults(func=cmd_status)

    stop = sub.add_parser("stop", help="Gửi tín hiệu Stop cho bot đang chạy")
    stop.set_defaults(func=cmd_stop)

    start = sub.add_parser("start", help="Xoá cờ Stop")
    start.set_defaults(func=cmd_start)

    reset = sub.add_parser("reset", help="Reset paper ledger")
    reset.add_argument("--yes", action="store_true")
    reset.set_defaults(func=cmd_reset)

    reset_learning = sub.add_parser(
        "reset-learning", help="Xoá forecast/calibration nhưng giữ candles/trades"
    )
    reset_learning.add_argument("--yes", action="store_true")
    reset_learning.set_defaults(func=cmd_reset_learning)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (FileNotFoundError, ValueError) as exc:
        print(f"Config/args lỗi: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

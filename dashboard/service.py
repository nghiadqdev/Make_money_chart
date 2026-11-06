"""Service layer for the local dashboard.

The bot itself runs in a child process (main.py run) so a dashboard restart
never corrupts the trading loop.  Commands are exchanged through
``var/control.json``; status is reconstructed from the persisted ledger
(``var/state.json`` + SQLite).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.loader import Settings, load_settings
from core.calibration import CalibrationManager
from core.database import Database
from core.logging_config import get_logger
from core.paper_engine import PaperEngine
from core.state_machine import ControlPlane
from core.types import ExitReason, Position
from risk_management.manager import RiskManager

logger = get_logger("dashboard")

REPO_ROOT = Path(__file__).resolve().parents[1]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DashboardService:
    def __init__(self, config_path: str = "config/settings.yaml"):
        self.repo_root = REPO_ROOT
        self.config_path = str(Path(config_path).resolve())
        self.settings: Settings = load_settings(self.config_path)
        self.control = ControlPlane(self.settings.storage.control_path)
        state_dir = Path(self.settings.storage.state_path).parent
        state_dir.mkdir(parents=True, exist_ok=True)
        Path(self.settings.storage.log_dir).mkdir(parents=True, exist_ok=True)
        self.pid_path = state_dir / "dashboard_bot.pid"
        self.bot_log_path = Path(self.settings.storage.log_dir) / "bot.log"
        self.stdout_path = Path(self.settings.storage.log_dir) / "dashboard_bot.stdout.log"
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._price_lock = threading.Lock()
        self._price_client = None
        self._price_cache: Dict[str, tuple[float, float]] = {}

    # ------------------------------------------------------------------ #
    # Process / control
    # ------------------------------------------------------------------ #
    def _pid(self) -> Optional[int]:
        if self._proc is not None:
            if self._proc.poll() is None:
                return self._proc.pid
            self._proc = None
            self._clear_pid()
            return None
        try:
            return int(self.pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def _write_pid(self, pid: int) -> None:
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.pid_path.write_text(str(pid), encoding="utf-8")

    def _clear_pid(self) -> None:
        try:
            self.pid_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def is_running(self) -> bool:
        pid = self._pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            self._clear_pid()
            return False
        except PermissionError:
            return True

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self.is_running():
                return {"ok": False, "message": "Bot đang chạy rồi."}

            self.control.request_start()
            self._clear_pid()
            self.bot_log_path.parent.mkdir(parents=True, exist_ok=True)
            stdout = open(self.stdout_path, "ab")
            cmd = [
                sys.executable,
                str(self.repo_root / "main.py"),
                "--config",
                self.config_path,
                "run",
            ]
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=str(self.repo_root),
                    stdout=stdout,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )
                self._write_pid(self._proc.pid)
            except Exception as exc:  # noqa: BLE001
                stdout.close()
                logger.exception("Không start được bot: %s", exc)
                return {"ok": False, "message": f"Không start được bot: {exc}"}
            finally:
                try:
                    stdout.close()
                except Exception:
                    pass

            return {
                "ok": True,
                "message": "Đã start paper bot. Đang load Kronos/KuCoin...",
                "pid": self._proc.pid,
            }

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            if not self.is_running():
                positions = len(self._positions_from_state())
                if positions:
                    return {
                        "ok": False,
                        "message": "Bot không chạy nhưng còn position. Dùng 'Chốt sổ ngay'.",
                    }
                return {"ok": False, "message": "Bot không chạy."}
            self.control.request_stop()
            return {
                "ok": True,
                "message": "Đã gửi Stop mềm: chặn lệnh mới, chờ SL/TP đóng position.",
            }

    def force_close(self, timeout_sec: float = 30.0) -> Dict[str, Any]:
        """Immediate flatten: ask running bot, or do it directly if stopped."""
        with self._lock:
            running = self.is_running()
            if running:
                self.control.request_force_close()
            else:
                return self._force_close_direct()

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            # Bot force-close xong sẽ clear control (read() == "start") và tự thoát.
            state = self._read_state()
            positions = (state or {}).get("positions", [])
            if not self.is_running():
                break
            if not positions and self.control.read() != "force_close":
                break
            time.sleep(0.5)

        state = self._read_state() or {}
        if not state.get("positions"):
            return {"ok": True, "message": "Đã chốt sổ: không còn position."}

        # Process đã dừng mà state vẫn còn position (crash) -> đóng trực tiếp.
        if not self.is_running():
            direct = self._force_close_direct()
            if direct.get("ok"):
                return direct
        return {
            "ok": False,
            "message": "Đã gửi force-close nhưng bot chưa xử lý xong. Xem log dashboard.",
        }

    def reset(self) -> Dict[str, Any]:
        if self.is_running():
            return {"ok": False, "message": "Phải Stop/Chốt sổ trước khi reset."}
        db = Database(self.settings.storage.db_path)
        try:
            engine = PaperEngine(
                self.settings.trading,
                db,
                self.settings.storage.state_path,
                symbol=self.settings.market.symbol,
            )
            db.reset_runtime()
            engine.reset()
            self.control.clear()
            db.insert_event("INFO", "dashboard_reset", {})
            return {"ok": True, "message": "Đã reset ledger về 100.0 USDT."}
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # Direct flatten when the bot is not running
    # ------------------------------------------------------------------ #
    def _force_close_direct(self) -> Dict[str, Any]:
        db = Database(self.settings.storage.db_path)
        engine = PaperEngine(
            self.settings.trading,
            db,
            self.settings.storage.state_path,
            symbol=self.settings.market.symbol,
        )
        try:
            if not engine.load_state() or not engine.positions:
                return {"ok": True, "message": "Không có position để chốt."}
            fallback = engine.last_prices.get(self.settings.market.symbol, 0.0)
            price = self._get_price(fallback) or fallback
            if not price:
                return {"ok": False, "message": "Không lấy được giá để chốt sổ."}
            engine.update_price(self.settings.market.symbol, price)
            risk = RiskManager(self.settings.trading)
            risk.restore(engine.get_meta("risk"))
            trades = engine.force_close_all(price, ExitReason.FORCE_CLOSE)
            for trade in trades:
                risk.on_trade_closed(trade)
            engine.set_meta("risk", risk.snapshot())
            engine.save_state()
            db.insert_event(
                "WARNING",
                "dashboard_force_close",
                {"price": price, "trades": [t.to_dict() for t in trades]},
            )
            pnl = sum(t.pnl for t in trades)
            return {
                "ok": True,
                "message": f"Đã chốt {len(trades)} position tại {price:.4f}; PnL {pnl:+.4f} USDT.",
                "price": price,
                "pnl": pnl,
                "trades": [t.to_dict() for t in trades],
            }
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def _read_state(self) -> Optional[Dict[str, Any]]:
        path = Path(self.settings.storage.state_path)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def _positions_from_state(self) -> List[Position]:
        state = self._read_state() or {}
        result: List[Position] = []
        for raw in state.get("positions", []):
            try:
                result.append(Position.from_dict(raw))
            except (KeyError, TypeError, ValueError):
                continue
        return result

    def _get_price(self, fallback: float = 0.0) -> Optional[float]:
        symbol = self.settings.market.symbol
        now = time.time()
        with self._price_lock:
            cached = self._price_cache.get(symbol)
            if cached and now - cached[0] < 5.0:
                return cached[1]
            try:
                if self._price_client is None:
                    import ccxt

                    self._price_client = ccxt.kucoin(
                        {"enableRateLimit": True, "timeout": 5000}
                    )
                ticker = self._price_client.fetch_ticker(symbol)
                price = float(ticker.get("last") or ticker.get("close") or 0.0)
                if price > 0:
                    self._price_cache[symbol] = (now, price)
                    return price
            except Exception as exc:  # noqa: BLE001
                logger.debug("Không lấy được giá dashboard: %s", exc)
            return fallback or None

    def _tail_file(self, path: Path, lines: int) -> List[str]:
        if not path.exists():
            return []
        try:
            with path.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 96_000))
                data = fh.read().decode("utf-8", errors="replace")
            return data.splitlines()[-lines:]
        except OSError:
            return []

    def _tail_logs(self, lines: int = 160) -> List[str]:
        # bot.log có log ứng dụng; nếu bot chưa kịp tạo (import/config lỗi)
        # thì đọc stdout của process con để hiển thị traceback.
        return self._tail_file(self.bot_log_path, lines) or self._tail_file(
            self.stdout_path, lines
        )

    def status(self) -> Dict[str, Any]:
        with self._lock:
            running = self.is_running()
        state = self._read_state() or {}
        positions = self._positions_from_state()
        fallback = float((state.get("last_prices") or {}).get(self.settings.market.symbol, 0.0))
        mark = self._get_price(fallback) or fallback

        free_cash = float(state.get("free_cash", self.settings.trading.initial_balance))
        realized = float(state.get("realized_pnl", 0.0))
        peak = float(state.get("peak_equity", self.settings.trading.initial_balance))
        margin = 0.0
        unrealized = 0.0
        pos_payload: List[Dict[str, Any]] = []
        for pos in positions:
            pos.last_price = mark or pos.last_price
            u = pos.unrealized_pnl(pos.last_price) if pos.last_price else 0.0
            margin += pos.margin
            unrealized += u
            item = pos.to_dict()
            item.update(
                {
                    "mark_price": pos.last_price,
                    "unrealized_pnl": u,
                    "pnl_pct": (u / pos.margin) if pos.margin else 0.0,
                }
            )
            pos_payload.append(item)

        equity = free_cash + margin + unrealized
        drawdown = max(0.0, 1.0 - equity / peak) if peak > 0 else 0.0

        risk = RiskManager(self.settings.trading)
        risk.restore((state.get("meta") or {}).get("risk"))
        can_trade, circuit_reason = risk.breaker.can_trade(None, equity)
        circuit_open = not can_trade

        calibration = CalibrationManager(self.settings.learning, self.settings.model)
        calibration.load()
        adaptive_lookback = int(
            (state.get("meta") or {}).get("adaptive_lookback", self.settings.model.lookback)
        )

        db = Database(self.settings.storage.db_path)
        try:
            stats = db.stats()
            trades = db.recent_trades(50)
            signals = db.recent_signals(30)
            orders = db.recent_orders(120)
            working_orders = db.working_orders()
            curve = db.equity_curve(300)
            learning_stats = db.forecast_stats(
                pred_len=self.settings.model.pred_len,
                lookback=self.settings.model.lookback,
            )
            recent_forecasts = db.recent_forecasts(
                20,
                pred_len=self.settings.model.pred_len,
                lookback=self.settings.model.lookback,
            )
            candle_count = db.count_candles(
                self.settings.market.symbol, self.settings.market.timeframe
            )
        finally:
            db.close()

        if not curve or abs(float(curve[-1].get("equity", 0)) - equity) > 1e-9:
            curve = curve + [{"ts": _now_iso(), "equity": equity}]

        control = self.control.read()
        if running and control == "force_close":
            display_state = "force_closing"
        elif running and control == "stop":
            display_state = "stopping"
        elif running:
            display_state = "running"
        elif positions:
            display_state = "stopped_with_positions"
        else:
            display_state = "stopped"

        return {
            "server_time": _now_iso(),
            "process": {
                "running": running,
                "pid": self._pid(),
                "control": control,
            },
            "state": display_state,
            "config": {
                "symbol": self.settings.market.symbol,
                "timeframe": self.settings.market.timeframe,
                "initial_balance": self.settings.trading.initial_balance,
                "taker_fee": self.settings.trading.taker_fee,
                "tranches": self.settings.trading.tranches,
                "risk_per_trade_pct": self.settings.trading.risk_per_trade_pct,
                "max_open_positions": self.settings.trading.max_open_positions,
                "long_threshold": calibration.long_threshold,
                "short_threshold": calibration.short_threshold,
                "lookback": self.settings.model.lookback,
                "lookback_current": adaptive_lookback,
                "lookback_max": self.settings.model.lookback_max,
                "lookback_growth": self.settings.model.lookback_growth,
                "pred_len": self.settings.model.pred_len,
                "n_paths": self.settings.model.n_paths,
                "max_drawdown_pct": self.settings.trading.max_drawdown_pct,
                "circuit_breaker_consecutive_losses": self.settings.trading.circuit_breaker_consecutive_losses,
                "circuit_breaker_cooldown_min": self.settings.trading.circuit_breaker_cooldown_min,
                "sl_atr_mult": self.settings.trading.sl_atr_mult,
                "tp_atr_mult": self.settings.trading.tp_atr_mult,
                "min_sl_pct": self.settings.trading.min_sl_pct,
                "max_sl_pct": self.settings.trading.max_sl_pct,
            },
            "market": {
                "symbol": self.settings.market.symbol,
                "timeframe": self.settings.market.timeframe,
                "mark_price": mark if mark else None,
            },
            "portfolio": {
                "equity": equity,
                "free_cash": free_cash,
                "realized_pnl": realized,
                "unrealized_pnl": unrealized,
                "exposure": margin,
                "open_positions": len(positions),
                "peak_equity": peak,
                "drawdown_pct": drawdown,
                "consecutive_losses": int(state.get("consecutive_losses", 0)),
                "circuit_open": circuit_open,
                "circuit_reason": circuit_reason,
            },
            "positions": pos_payload,
            "risk": risk.circuit_status(),
            "stats": stats,
            "recent_trades": trades,
            "recent_orders": orders,
            "working_orders": working_orders,
            "recent_signals": signals,
            "learning": {
                "stats": learning_stats,
                "calibration": calibration.snapshot(),
                "recent_forecasts": recent_forecasts,
                "candles_stored": candle_count,
            },
            "equity_curve": curve,
            "logs": self._tail_logs(),
            "last_trade": trades[0] if trades else None,
            "last_order": orders[-1] if orders else None,
        }

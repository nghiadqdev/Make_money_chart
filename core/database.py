"""SQLite persistence: closed trades, signals, equity curve, bot events."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from core.types import ClosedTrade, Order, OrderStatus, PortfolioSnapshot, TradeSignal

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id          TEXT PRIMARY KEY,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    qty         REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price  REAL NOT NULL,
    sl          REAL NOT NULL,
    tp          REAL NOT NULL,
    margin      REAL NOT NULL,
    entry_fee   REAL NOT NULL,
    exit_fee    REAL NOT NULL,
    gross_pnl   REAL NOT NULL,
    pnl         REAL NOT NULL,
    return_pct  REAL NOT NULL,
    opened_at   TEXT NOT NULL,
    closed_at   TEXT NOT NULL,
    reason      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades(closed_at);

CREATE TABLE IF NOT EXISTS orders (
    id              TEXT PRIMARY KEY,
    position_id     TEXT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    qty             REAL NOT NULL,
    requested_price REAL NOT NULL,
    fill_price      REAL,
    status          TEXT NOT NULL,
    fee             REAL NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    filled_at       TEXT,
    reason          TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_position ON orders(position_id);

CREATE TABLE IF NOT EXISTS candles (
    symbol    TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts        TEXT NOT NULL,
    open      REAL NOT NULL,
    high      REAL NOT NULL,
    low       REAL NOT NULL,
    close     REAL NOT NULL,
    volume    REAL NOT NULL,
    amount    REAL NOT NULL,
    PRIMARY KEY (symbol, timeframe, ts)
);
CREATE INDEX IF NOT EXISTS idx_candles_lookup ON candles(symbol, timeframe, ts);

CREATE TABLE IF NOT EXISTS forecasts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    lookback        INTEGER NOT NULL,
    pred_len        INTEGER NOT NULL,
    last_price      REAL NOT NULL,
    predicted_price REAL NOT NULL,
    expected_return REAL NOT NULL,
    p_up            REAL NOT NULL,
    p_down          REAL NOT NULL,
    score           REAL NOT NULL,
    confidence      REAL NOT NULL,
    signal          TEXT NOT NULL,
    decision        TEXT,
    decision_reason TEXT,
    threshold_long  REAL,
    threshold_short REAL,
    horizon_end     TEXT NOT NULL,
    actual_price    REAL,
    actual_return   REAL,
    direction_up    INTEGER,
    direction_correct INTEGER,
    brier           REAL,
    abs_error       REAL,
    evaluated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_forecasts_eval ON forecasts(evaluated_at, horizon_end);

CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    signal          TEXT NOT NULL,
    p_up            REAL,
    p_down          REAL,
    expected_return REAL,
    score           REAL,
    confidence      REAL,
    price           REAL,
    reasons         TEXT,
    decision        TEXT,
    decision_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);

CREATE TABLE IF NOT EXISTS equity (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    equity         REAL NOT NULL,
    free_cash      REAL NOT NULL,
    realized       REAL NOT NULL,
    unrealized     REAL NOT NULL,
    exposure       REAL NOT NULL,
    open_positions INTEGER NOT NULL,
    drawdown_pct   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity(ts);

CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    level  TEXT NOT NULL,
    event  TEXT NOT NULL,
    details TEXT
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def insert_trade(self, trade: ClosedTrade) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO trades
                (id, symbol, side, qty, entry_price, exit_price, sl, tp, margin,
                 entry_fee, exit_fee, gross_pnl, pnl, return_pct,
                 opened_at, closed_at, reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    trade.id, trade.symbol, trade.side.value, trade.qty,
                    trade.entry_price, trade.exit_price, trade.sl, trade.tp,
                    trade.margin, trade.entry_fee, trade.exit_fee,
                    trade.gross_pnl, trade.pnl, trade.return_pct,
                    trade.opened_at, trade.closed_at, trade.reason,
                ),
            )

    def insert_order(self, order: Order) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO orders
                (id, position_id, symbol, side, order_type, qty, requested_price,
                 fill_price, status, fee, created_at, filled_at, reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    order.id, order.position_id, order.symbol, order.side.value,
                    order.order_type.value, order.qty, order.requested_price,
                    order.fill_price, order.status.value, order.fee,
                    order.created_at, order.filled_at, order.reason,
                ),
            )

    def update_order(
        self,
        order_id: str,
        status: OrderStatus | str,
        fill_price: Optional[float] = None,
        fee: Optional[float] = None,
        filled_at: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        status_value = status.value if isinstance(status, OrderStatus) else str(status)
        fields = ["status = ?"]
        params: List[Any] = [status_value]
        if fill_price is not None:
            fields.append("fill_price = ?")
            params.append(float(fill_price))
        if fee is not None:
            fields.append("fee = ?")
            params.append(float(fee))
        if filled_at is not None:
            fields.append("filled_at = ?")
            params.append(filled_at)
        if reason is not None:
            fields.append("reason = ?")
            params.append(reason)
        params.append(order_id)
        with self.conn:
            self.conn.execute(
                f"UPDATE orders SET {', '.join(fields)} WHERE id = ?", params
            )

    def upsert_candles(self, df: pd.DataFrame, symbol: str, timeframe: str) -> None:
        if df is None or df.empty:
            return
        rows = []
        for r in df.itertuples(index=False):
            ts = pd.Timestamp(getattr(r, "timestamp")).isoformat()
            rows.append((
                symbol, timeframe, ts,
                float(getattr(r, "open")), float(getattr(r, "high")),
                float(getattr(r, "low")), float(getattr(r, "close")),
                float(getattr(r, "volume")),
                float(getattr(r, "amount", getattr(r, "volume") * getattr(r, "close"))),
            ))
        with self.conn:
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO candles
                (symbol, timeframe, ts, open, high, low, close, volume, amount)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )

    def count_candles(self, symbol: str, timeframe: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM candles WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        ).fetchone()
        return int(row["n"])

    def get_candle_at_or_after(self, symbol: str, timeframe: str, ts: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            """SELECT * FROM candles WHERE symbol=? AND timeframe=? AND ts >= ?
               ORDER BY ts LIMIT 1""",
            (symbol, timeframe, ts),
        ).fetchone()
        return dict(row) if row else None

    def insert_forecast(self, data: Dict[str, Any]) -> int:
        cols = [
            "created_at", "symbol", "timeframe", "lookback", "pred_len",
            "last_price", "predicted_price", "expected_return", "p_up",
            "p_down", "score", "confidence", "signal", "decision",
            "decision_reason", "threshold_long", "threshold_short", "horizon_end",
        ]
        vals = [data.get(c) for c in cols]
        with self.conn:
            cur = self.conn.execute(
                f"INSERT INTO forecasts ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
                vals,
            )
            return int(cur.lastrowid)

    def pending_forecasts(self, now_iso: str, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM forecasts
               WHERE evaluated_at IS NULL AND horizon_end <= ?
               ORDER BY horizon_end LIMIT ?""",
            (now_iso, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_forecast_eval(
        self,
        forecast_id: int,
        actual_price: float,
        actual_return: float,
        direction_up: bool,
        direction_correct: bool,
        brier: float,
        abs_error: float,
        evaluated_at: str,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE forecasts SET actual_price=?, actual_return=?,
                   direction_up=?, direction_correct=?, brier=?, abs_error=?,
                   evaluated_at=? WHERE id=?""",
                (
                    actual_price, actual_return, int(direction_up),
                    int(direction_correct), brier, abs_error, evaluated_at,
                    forecast_id,
                ),
            )

    @staticmethod
    def _forecast_filter(pred_len: Optional[int], lookback: Optional[int]) -> tuple[str, list]:
        clauses, params = [], []
        if pred_len is not None:
            clauses.append("pred_len = ?")
            params.append(int(pred_len))
        if lookback is not None:
            clauses.append("lookback = ?")
            params.append(int(lookback))
        return (" AND " + " AND ".join(clauses)) if clauses else "", params

    def forecast_stats(self, pred_len: Optional[int] = None, lookback: Optional[int] = None) -> Dict[str, Any]:
        extra, params = self._forecast_filter(pred_len, lookback)
        row = self.conn.execute(
            f"""SELECT COUNT(*) AS evaluated,
                      AVG(brier) AS brier,
                      AVG(CASE WHEN direction_correct=1 THEN 1.0 ELSE 0.0 END) AS hit_rate,
                      AVG(abs_error) AS mae
               FROM forecasts WHERE evaluated_at IS NOT NULL{extra}""",
            params,
        ).fetchone()
        data = dict(row)
        data["evaluated"] = int(data.get("evaluated") or 0)
        data["brier"] = float(data["brier"]) if data.get("brier") is not None else None
        data["hit_rate"] = float(data["hit_rate"]) if data.get("hit_rate") is not None else None
        data["mae"] = float(data["mae"]) if data.get("mae") is not None else None
        row2 = self.conn.execute(
            f"""SELECT COUNT(*) AS total,
                      SUM(CASE WHEN evaluated_at IS NULL THEN 1 ELSE 0 END) AS pending
               FROM forecasts WHERE 1=1{extra}""",
            params,
        ).fetchone()
        data["total"] = int(row2["total"] or 0)
        data["pending"] = int(row2["pending"] or 0)
        return data

    def recent_forecasts(
        self, limit: int = 30, pred_len: Optional[int] = None, lookback: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        extra, params = self._forecast_filter(pred_len, lookback)
        rows = self.conn.execute(
            f"SELECT * FROM forecasts WHERE 1=1{extra} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def evaluated_forecasts(
        self, limit: int = 300, pred_len: Optional[int] = None, lookback: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        extra, params = self._forecast_filter(pred_len, lookback)
        rows = self.conn.execute(
            f"""SELECT * FROM forecasts WHERE evaluated_at IS NOT NULL{extra}
               ORDER BY id DESC LIMIT ?""",
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def clear_forecasts(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM forecasts")

    def insert_signal(
        self,
        signal: TradeSignal,
        decision: str,
        decision_reason: str,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO signals
                (ts, symbol, signal, p_up, p_down, expected_return, score,
                 confidence, price, reasons, decision, decision_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    signal.timestamp, signal.symbol, signal.signal.value,
                    signal.p_up, signal.p_down, signal.expected_return,
                    signal.score, signal.confidence, signal.price,
                    json.dumps(signal.reasons, ensure_ascii=False),
                    decision, decision_reason,
                ),
            )

    def insert_equity(self, ts: str, snapshot: PortfolioSnapshot) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO equity
                (ts, equity, free_cash, realized, unrealized, exposure,
                 open_positions, drawdown_pct)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    ts, snapshot.equity, snapshot.free_cash,
                    snapshot.realized_pnl, snapshot.unrealized_pnl,
                    snapshot.exposure, snapshot.open_positions,
                    snapshot.drawdown_pct,
                ),
            )

    def insert_event(self, level: str, event: str, details: Optional[Dict[str, Any]] = None) -> None:
        from core.types import utc_now_iso

        with self.conn:
            self.conn.execute(
                "INSERT INTO events (ts, level, event, details) VALUES (?,?,?,?)",
                (utc_now_iso(), level, event, json.dumps(details or {}, ensure_ascii=False)),
            )

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def recent_trades(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM trades ORDER BY closed_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def equity_curve(self, limit: int = 500) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM equity ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def recent_orders(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def working_orders(self, position_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if position_id:
            rows = self.conn.execute(
                "SELECT * FROM orders WHERE status = 'working' AND position_id = ?",
                (position_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM orders WHERE status = 'working' ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def recent_signals(self, limit: int = 30) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        result = []
        for row in reversed(rows):
            data = dict(row)
            try:
                data["reasons"] = json.loads(data.get("reasons") or "[]")
            except (TypeError, json.JSONDecodeError):
                pass
            result.append(data)
        return result

    def stats(self) -> Dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS trades,
                   COALESCE(SUM(pnl), 0) AS realized_pnl,
                   COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0) AS wins,
                   COALESCE(SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END), 0) AS losses
            FROM trades
            """
        ).fetchone()
        data = dict(row)
        trades = data["trades"] or 0
        data["win_rate"] = (data["wins"] / trades) if trades else 0.0
        return data

    def reset_runtime(self) -> None:
        """Xoá lịch sử paper runtime (giữ schema)."""
        with self.conn:
            self.conn.execute("DELETE FROM orders")
            self.conn.execute("DELETE FROM forecasts")
            self.conn.execute("DELETE FROM candles")
            self.conn.execute("DELETE FROM trades")
            self.conn.execute("DELETE FROM signals")
            self.conn.execute("DELETE FROM equity")
            self.conn.execute("DELETE FROM events")

    def close(self) -> None:
        self.conn.close()

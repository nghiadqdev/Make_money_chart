"""Thin, resilient CCXT wrapper for KuCoin public market data.

The paper engine only needs public OHLCV/ticker endpoints, so API keys are
optional.  Live order placement is intentionally **not** implemented.
"""

from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional

import ccxt
import pandas as pd

from config.loader import ExchangeConfig
from core.logging_config import get_logger

logger = get_logger("exchange")

RETRYABLE = (
    ccxt.NetworkError,
    ccxt.ExchangeNotAvailable,
    ccxt.RequestTimeout,
    ccxt.RateLimitExceeded,
    ccxt.DDoSProtection,
)


class ExchangeError(RuntimeError):
    pass


class KuCoinExchange:
    def __init__(self, config: ExchangeConfig):
        self.config = config
        self.client: Optional[ccxt.Exchange] = None
        self._connect_client()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def _connect_client(self) -> None:
        if self.config.id != "kucoin":
            raise ExchangeError("Bot này được thiết kế cho KuCoin (ccxt id='kucoin')")

        client = ccxt.kucoin(
            {
                "apiKey": self.config.api_key or None,
                "secret": self.config.api_secret or None,
                "password": self.config.api_passphrase or None,
                "enableRateLimit": self.config.enable_rate_limit,
                "timeout": self.config.timeout_ms,
                "options": {"defaultType": "spot"},
            }
        )
        if self.config.sandbox:
            if not (self.config.api_key and self.config.api_secret):
                raise ExchangeError("sandbox=true cần API key/secret")
            client.set_sandbox_mode(True)
        self.client = client

    def connect(self) -> None:
        assert self.client is not None
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                self.client.load_markets()
                logger.info("Kết nối KuCoin OK | %d markets", len(self.client.markets or {}))
                return
            except RETRYABLE as exc:
                last_error = exc
                self._sleep_backoff(attempt, f"connect: {exc}")
            except ccxt.BaseError as exc:
                raise ExchangeError(f"KuCoin từ chối kết nối: {exc}") from exc
        raise ExchangeError(f"Không kết nối được KuCoin sau {self.config.max_retries} lần: {last_error}")

    def reconnect(self) -> None:
        logger.warning("Đang tạo lại kết nối KuCoin...")
        try:
            if self.client is not None:
                self.client.close()
        except Exception:
            pass
        self._connect_client()
        self.connect()

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    def _sleep_backoff(self, attempt: int, message: str) -> None:
        delay = min(self.config.backoff_base_sec * (2 ** (attempt - 1)), 60.0)
        delay += random.uniform(0, delay * 0.25)
        logger.warning("Lỗi mạng (%s), thử lại sau %.1fs [%d/%d]", message, delay, attempt, self.config.max_retries)
        time.sleep(delay)

    def _retry(self, fn, description: str):
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                return fn()
            except RETRYABLE as exc:
                last_error = exc
                self._sleep_backoff(attempt, f"{description}: {exc}")
            except ccxt.BadSymbol as exc:
                raise ExchangeError(f"Symbol không hợp lệ trên KuCoin: {exc}") from exc
            except ccxt.BaseError as exc:
                raise ExchangeError(f"{description} thất bại: {exc}") from exc
        raise ExchangeError(f"{description} thất bại sau {self.config.max_retries} lần: {last_error}")

    # ------------------------------------------------------------------ #
    # Market data
    # ------------------------------------------------------------------ #
    def validate_symbol(self, symbol: str) -> None:
        assert self.client is not None
        markets = self.client.markets or {}
        if symbol not in markets:
            sample = ", ".join(sorted(list(markets))[:5])
            raise ExchangeError(f"Không tìm thấy {symbol}. Ví dụ market: {sample}")

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 600,
        since: Optional[int] = None,
    ) -> pd.DataFrame:
        assert self.client is not None
        raw = self._retry(
            lambda: self.client.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit),
            f"fetch_ohlcv {symbol} {timeframe}",
        )
        if not raw:
            raise ExchangeError(f"KuCoin trả về 0 nến cho {symbol} {timeframe}")

        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_localize(None)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = (
            df.dropna()
            .drop_duplicates("timestamp", keep="last")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )
        # CCXT không trả amount; paper engine không bắt buộc nhưng Kronos dùng làm feature.
        df["amount"] = df["volume"] * df["close"]
        return df

    def fetch_ohlcv_paginated(
        self,
        symbol: str,
        timeframe: str,
        since_ms: int,
        max_batches: int = 50,
    ) -> pd.DataFrame:
        """Backfill lịch sử (nếu cần train/backtest)."""
        assert self.client is not None
        frames: List[pd.DataFrame] = []
        cursor = since_ms
        for _ in range(max_batches):
            batch = self.fetch_ohlcv(symbol, timeframe, limit=1000, since=cursor)
            if batch.empty:
                break
            frames.append(batch)
            next_cursor = int(batch["timestamp"].iloc[-1].timestamp() * 1000) + 1
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(batch) < 1000:
                break
        if not frames:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "amount"])
        out = pd.concat(frames, ignore_index=True)
        return out.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)

    def fetch_last_price(self, symbol: str) -> float:
        assert self.client is not None
        ticker = self._retry(lambda: self.client.fetch_ticker(symbol), f"fetch_ticker {symbol}")
        price = ticker.get("last") or ticker.get("close")
        if price is None:
            raise ExchangeError(f"Ticker {symbol} không có giá last/close")
        return float(price)

    def market_metadata(self, symbol: str) -> Dict[str, Any]:
        assert self.client is not None
        market = (self.client.markets or {}).get(symbol, {})
        return {
            "symbol": symbol,
            "base": market.get("base"),
            "quote": market.get("quote"),
            "min_amount": (market.get("limits", {}).get("amount", {}) or {}).get("min"),
            "min_cost": (market.get("limits", {}).get("cost", {}) or {}).get("min"),
            "price_precision": market.get("precision", {}).get("price"),
        }

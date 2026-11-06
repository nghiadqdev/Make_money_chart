"""Typed configuration loader for the Kronos paper-trading bot.

Priority: YAML file -> environment variables -> dataclass defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass, replace
from pathlib import Path
from typing import Any, Dict, Type, TypeVar, get_type_hints

import yaml

T = TypeVar("T")


@dataclass(frozen=True)
class ExchangeConfig:
    id: str = "kucoin"
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""
    sandbox: bool = False
    timeout_ms: int = 20_000
    enable_rate_limit: bool = True
    max_retries: int = 5
    backoff_base_sec: float = 2.0


@dataclass(frozen=True)
class MarketConfig:
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    ohlcv_limit: int = 600
    poll_interval_sec: int = 30
    closed_candle_only: bool = True


@dataclass(frozen=True)
class ModelConfig:
    tokenizer_id: str = "NeoQuasar/Kronos-Tokenizer-base"
    model_id: str = "NeoQuasar/Kronos-small"
    device: str = "auto"
    max_context: int = 512
    lookback: int = 400
    lookback_growth: int = 0
    lookback_max: int = 512
    pred_len: int = 24
    n_paths: int = 32
    temperature: float = 1.0
    top_p: float = 0.9
    long_threshold: float = 0.62
    short_threshold: float = 0.38
    min_expected_return: float = 0.003
    fee_buffer_pct: float = 0.0025


@dataclass(frozen=True)
class StrategyConfig:
    ema_fast: int = 50
    ema_slow: int = 200
    atr_period: int = 14
    require_trend_alignment: bool = True


@dataclass(frozen=True)
class TradingConfig:
    initial_balance: float = 100.0
    taker_fee: float = 0.001
    tranches: int = 4
    max_open_positions: int = 4
    risk_per_trade_pct: float = 0.01
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 2.5
    min_sl_pct: float = 0.006
    max_sl_pct: float = 0.030
    min_notional: float = 5.0
    allow_short: bool = True
    max_drawdown_pct: float = 0.15
    circuit_breaker_consecutive_losses: int = 3
    circuit_breaker_cooldown_min: int = 120
    shutdown_force_close_sec: int = 0


@dataclass(frozen=True)
class LearningConfig:
    enabled: bool = True
    auto_calibrate: bool = True
    min_samples: int = 30
    window: int = 200
    min_threshold: float = 0.55
    max_threshold: float = 0.75
    step: float = 0.01
    calibration_path: str = "var/calibration.json"


@dataclass(frozen=True)
class StorageConfig:
    db_path: str = "var/kronos_bot.db"
    state_path: str = "var/state.json"
    control_path: str = "var/control.json"
    log_dir: str = "logs"
    log_level: str = "INFO"


@dataclass(frozen=True)
class Settings:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    market: MarketConfig = field(default_factory=MarketConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)


def _build(cls: Type[T], raw: Dict[str, Any] | None) -> T:
    raw = raw or {}
    hints = get_type_hints(cls)
    kwargs: Dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in raw:
            continue
        value = raw[f.name]
        ftype = hints.get(f.name, f.type)
        if is_dataclass(ftype) and isinstance(value, dict):
            kwargs[f.name] = _build(ftype, value)
        elif ftype is bool and isinstance(value, str):
            kwargs[f.name] = value.strip().lower() in {"1", "true", "yes", "on"}
        elif ftype in (int, float) and isinstance(value, str):
            kwargs[f.name] = ftype(value)
        else:
            kwargs[f.name] = value

    # Chỉ ExchangeConfig có secret; cho phép override bằng ENV.
    if cls is ExchangeConfig:
        for attr, env_name in {
            "api_key": "KUCOIN_API_KEY",
            "api_secret": "KUCOIN_API_SECRET",
            "api_passphrase": "KUCOIN_API_PASSPHRASE",
        }.items():
            if os.getenv(env_name):
                kwargs[attr] = os.environ[env_name]

    return cls(**kwargs)


def _apply_env(settings: Settings) -> Settings:
    market = settings.market
    if os.getenv("KRONOS_BOT_SYMBOL"):
        market = replace(market, symbol=os.environ["KRONOS_BOT_SYMBOL"])
    if os.getenv("KRONOS_BOT_TIMEFRAME"):
        market = replace(market, timeframe=os.environ["KRONOS_BOT_TIMEFRAME"])

    model = settings.model
    if os.getenv("KRONOS_BOT_DEVICE"):
        model = replace(model, device=os.environ["KRONOS_BOT_DEVICE"])

    return replace(settings, market=market, model=model)


def load_settings(path: str | Path = "config/settings.yaml") -> Settings:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config không tồn tại: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    settings = Settings(
        exchange=_build(ExchangeConfig, raw.get("exchange")),
        market=_build(MarketConfig, raw.get("market")),
        model=_build(ModelConfig, raw.get("model")),
        strategy=_build(StrategyConfig, raw.get("strategy")),
        trading=_build(TradingConfig, raw.get("trading")),
        learning=_build(LearningConfig, raw.get("learning")),
        storage=_build(StorageConfig, raw.get("storage")),
    )
    settings = _apply_env(settings)
    _validate(settings)
    return settings


def _validate(settings: Settings) -> None:
    if settings.market.timeframe not in {"15m", "30m", "1h", "4h", "1d"}:
        raise ValueError("market.timeframe phải là 15m, 30m, 1h, 4h hoặc 1d")
    if not 0 < settings.trading.taker_fee < 0.02:
        raise ValueError("trading.taker_fee phải nằm trong (0, 0.02)")
    if settings.trading.tranches < 1:
        raise ValueError("trading.tranches phải >= 1")
    if not 0 < settings.trading.risk_per_trade_pct <= 0.05:
        raise ValueError("risk_per_trade_pct phải nằm trong (0, 0.05]")
    if settings.model.n_paths < 4:
        raise ValueError("model.n_paths phải >= 4 để tính xác suất")
    if settings.model.lookback > settings.model.max_context:
        raise ValueError("model.lookback không được vượt quá max_context")
    if settings.model.lookback_max > settings.model.max_context:
        raise ValueError("lookback_max không được vượt quá max_context")
    if settings.model.lookback_growth < 0:
        raise ValueError("lookback_growth phải >= 0")
    if settings.model.lookback_max < settings.model.lookback:
        raise ValueError("lookback_max phải >= lookback")
    if settings.model.long_threshold <= 0.5:
        raise ValueError("long_threshold phải > 0.5")
    if settings.model.short_threshold >= 0.5:
        raise ValueError("short_threshold phải < 0.5")
    if settings.trading.shutdown_force_close_sec < 0:
        raise ValueError("shutdown_force_close_sec phải >= 0")
    if not 0.5 < settings.learning.min_threshold <= settings.learning.max_threshold < 1.0:
        raise ValueError("learning thresholds không hợp lệ")


__all__ = [
    "Settings",
    "ExchangeConfig",
    "MarketConfig",
    "ModelConfig",
    "StrategyConfig",
    "TradingConfig",
    "StorageConfig",
    "load_settings",
]

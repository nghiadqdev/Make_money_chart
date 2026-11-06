"""Conservative threshold calibration from realized forecast outcomes.

This does **not** update Kronos weights. It only adjusts the probability
thresholds used by the strategy, and only after enough evaluated forecasts.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from config.loader import LearningConfig, ModelConfig
from core.database import Database
from core.logging_config import get_logger

logger = get_logger("calibration")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CalibrationManager:
    def __init__(self, config: LearningConfig, model_config: ModelConfig):
        self.config = config
        self.model_config = model_config
        self.path = Path(config.calibration_path)
        self.long_threshold = float(model_config.long_threshold)
        self.short_threshold = float(model_config.short_threshold)
        self.meta: Dict[str, Any] = {"samples": 0, "updated_at": None}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.long_threshold = float(data.get("long_threshold", self.long_threshold))
            self.short_threshold = float(data.get("short_threshold", self.short_threshold))
            self.meta = data
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Không đọc được calibration %s: %s", self.path, exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "long_threshold": round(self.long_threshold, 4),
            "short_threshold": round(self.short_threshold, 4),
            **self.meta,
            "updated_at": self.meta.get("updated_at") or _now_iso(),
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "long_threshold": self.long_threshold,
            "short_threshold": self.short_threshold,
            **self.meta,
        }

    def maybe_update(self, db: Database, force: bool = False) -> Dict[str, Any]:
        if not self.config.enabled or not self.config.auto_calibrate:
            return self.snapshot()

        # Chỉ dùng forecast cùng cấu hình hiện tại (pred_len + lookback).
        rows = db.evaluated_forecasts(
            self.config.window,
            pred_len=self.model_config.pred_len,
            lookback=self.model_config.lookback,
        )
        if len(rows) < self.config.min_samples:
            self.meta.update({"samples": len(rows), "status": "waiting_for_samples"})
            return self.snapshot()

        long_cut = self.long_threshold
        short_cut = 1.0 - self.short_threshold
        long_rows = [r for r in rows if float(r["p_up"]) >= long_cut]
        short_rows = [r for r in rows if float(r["p_down"]) >= short_cut]

        # Cần đủ mẫu trong từng nhánh để tránh nhiễu.
        min_branch = max(5, self.config.min_samples // 2)
        long_precision = (
            sum(1 for r in long_rows if float(r["actual_return"]) > 0) / len(long_rows)
            if len(long_rows) >= min_branch else None
        )
        short_precision = (
            sum(1 for r in short_rows if float(r["actual_return"]) < 0) / len(short_rows)
            if len(short_rows) >= min_branch else None
        )

        changed = False
        if long_precision is not None:
            if long_precision < 0.52:
                new = min(self.config.max_threshold, self.long_threshold + self.config.step)
            elif long_precision > 0.62:
                new = max(self.config.min_threshold, self.long_threshold - self.config.step)
            else:
                new = self.long_threshold
            if abs(new - self.long_threshold) > 1e-9:
                logger.info("Calibration long: %.3f -> %.3f (precision=%.3f)",
                            self.long_threshold, new, long_precision)
                self.long_threshold = new
                changed = True

        if short_precision is not None:
            # short_threshold thấp hơn = yêu cầu p_down cao hơn = chọn lọc hơn.
            # Miền short đối xứng với long: [1-max_threshold, 1-min_threshold].
            short_min = 1.0 - self.config.max_threshold
            short_max = 1.0 - self.config.min_threshold
            if short_precision < 0.52:
                new = max(short_min, self.short_threshold - self.config.step)
            elif short_precision > 0.62:
                new = min(short_max, self.short_threshold + self.config.step)
            else:
                new = self.short_threshold
            if abs(new - self.short_threshold) > 1e-9:
                logger.info("Calibration short: %.3f -> %.3f (precision=%.3f)",
                            self.short_threshold, new, short_precision)
                self.short_threshold = new
                changed = True

        self.meta.update({
            "samples": len(rows),
            "long_samples": len(long_rows),
            "short_samples": len(short_rows),
            "long_precision": long_precision,
            "short_precision": short_precision,
            "status": "updated" if changed else "stable",
        })
        if changed or force:
            self.meta["updated_at"] = _now_iso()
            self.save()
        return self.snapshot()

    def reset(self) -> None:
        self.long_threshold = float(self.model_config.long_threshold)
        self.short_threshold = float(self.model_config.short_threshold)
        self.meta = {"samples": 0, "updated_at": None, "status": "reset"}
        if self.path.exists():
            self.path.unlink()

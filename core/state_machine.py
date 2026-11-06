"""Start/Stop lifecycle and an external control file.

Lifecycle:

    START --(SIGINT / `main.py stop`)--> STOPPING --(no open positions)--> STOPPED

In STOPPING the bot never opens new positions; it keeps checking SL/TP for
existing positions until the ledger is flat.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from core.logging_config import get_logger
from core.types import BotState

logger = get_logger("state")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateMachine:
    def __init__(self, initial: BotState = BotState.START):
        self._state = initial
        self.since = _now_iso()

    @property
    def state(self) -> BotState:
        return self._state

    @property
    def allow_new_entries(self) -> bool:
        return self._state is BotState.START

    @property
    def is_stopping(self) -> bool:
        return self._state is BotState.STOPPING

    @property
    def is_stopped(self) -> bool:
        return self._state is BotState.STOPPED

    def _transition(self, new_state: BotState, reason: str) -> None:
        if new_state is self._state:
            return
        logger.info("STATE %s -> %s (%s)", self._state.value, new_state.value, reason)
        self._state = new_state
        self.since = _now_iso()

    def request_stop(self, reason: str = "requested") -> None:
        if self._state is BotState.START:
            self._transition(BotState.STOPPING, reason)

    def mark_stopped(self, reason: str = "flat") -> None:
        if self._state is not BotState.STOPPED:
            self._transition(BotState.STOPPED, reason)

    def to_dict(self) -> dict:
        return {"state": self._state.value, "since": self.since}


class ControlPlane:
    """Small JSON file so `python main.py stop` can signal a running bot."""

    def __init__(self, path: str | Path = "var/control.json"):
        self.path = Path(path)

    def read(self) -> str:
        if not self.path.exists():
            return "start"
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            value = str(data.get("state", "start")).lower()
            return value if value in {"start", "stop", "force_close"} else "start"
        except (OSError, json.JSONDecodeError):
            return "start"

    def _write(self, state: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump({"state": state, "updated_at": _now_iso()}, fh, indent=2)
        os.replace(tmp, self.path)

    def request_start(self) -> None:
        self._write("start")

    def request_stop(self) -> None:
        self._write("stop")

    def request_force_close(self) -> None:
        self._write("force_close")

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()

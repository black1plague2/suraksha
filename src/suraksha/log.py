"""Structured JSON logging. Every agent logs through `get_logger(__name__)`.

Logs go to stderr and to logs/runtime/suraksha.jsonl (rotated by size, small footprint).
Use: log.info("match_found", extra={"ctx": {"request_id": rid, "match_type": "EXACT"}})
"""
from __future__ import annotations

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        ctx = getattr(record, "ctx", None)
        if ctx:
            out["ctx"] = ctx
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger("suraksha")
    root.setLevel(os.getenv("SURAKSHA_LOG_LEVEL", "INFO"))
    fmt = JsonFormatter()
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    log_dir = Path(os.getenv("SURAKSHA_LOG_DIR", Path(__file__).resolve().parents[2] / "logs" / "runtime"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_dir / "suraksha.jsonl", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass  # read-only FS (e.g. Snowflake Streamlit) -> stderr only
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    _configure()
    return logging.getLogger(name if name.startswith("suraksha") else f"suraksha.{name}")

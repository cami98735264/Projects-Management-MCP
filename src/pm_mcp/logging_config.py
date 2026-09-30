"""Structured (JSON-lines) logging to stderr — stdout is reserved for the MCP stdio protocol."""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

ROOT_LOGGER = "pm_mcp"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str | None = None) -> None:
    logger = logging.getLogger(ROOT_LOGGER)
    if any(getattr(h, "_pm_mcp", False) for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler._pm_mcp = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(level or os.environ.get("PM_MCP_LOG_LEVEL", "INFO"))
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name if name.startswith(ROOT_LOGGER) else f"{ROOT_LOGGER}.{name}")


def log_stage(logger: logging.Logger, stage: str, **fields: Any) -> None:
    logger.info(stage, extra={"fields": {"stage": stage, **fields}})

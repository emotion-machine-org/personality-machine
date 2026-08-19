import json
import logging
import os
import sys
from datetime import UTC, datetime
from enum import Enum
from logging.config import dictConfig
from typing import Any, Dict

LOG_FORMAT_DEBUG = "%(levelname)s:%(message)s:%(pathname)s:%(funcName)s:%(lineno)d"
LOG_FORMAT_PLAIN = "%(asctime)s %(levelname)s %(name)s - %(message)s"

DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVEL_ENV_VAR = "LOG_LEVEL"
ENV_VAR = "ENV"
LOCAL_ENV_VALUES = {"local", "dev", "development"}

NOISY_LOGGERS = (
    "websockets",
    "websockets.client",
    "websockets.server",
    "websockets.protocol",
    "websocket",
)

PRESET_LOGGER_LEVELS: Dict[str, int] = {
    "turn_manager": logging.INFO,
    "agent": logging.INFO,
}


class LogLevels(str, Enum):
    info = "INFO"
    warn = "WARN"
    error = "ERROR"
    debug = "DEBUG"


class HealthCheckFilter(logging.Filter):
    """Filter out noisy health check requests from access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        # Filter out /healthz requests (Render health checks)
        return "/healthz" not in message


class JsonFormatter(logging.Formatter):
    """JSON formatter for makings logs structured and easy to parse by log analysis tools."""

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        log_record: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if record.exc_info:
            log_record["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(log_record)


def _coerce_log_level(level: str | None) -> str:
    if not level:
        return DEFAULT_LOG_LEVEL

    level_upper = level.upper()
    valid_levels = {lvl.value for lvl in LogLevels}
    if level_upper not in valid_levels:
        return LogLevels.error.value
    return level_upper


def _should_use_json_formatter() -> bool:
    env = os.getenv(ENV_VAR, "local").lower()
    return env not in LOCAL_ENV_VALUES


def _build_logging_config(level: str) -> Dict[str, Any]:
    use_json = _should_use_json_formatter()
    formatter_name = "json" if use_json else "plain"

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": JsonFormatter,
            },
            "plain": {
                "format": LOG_FORMAT_PLAIN,
            },
            "debug": {
                "format": LOG_FORMAT_DEBUG,
            },
        },
        "filters": {
            "health_check": {
                "()": HealthCheckFilter,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": level,
                "formatter": formatter_name,
                "stream": "ext://sys.stdout",
            },
            "console_filtered": {
                "class": "logging.StreamHandler",
                "level": level,
                "formatter": formatter_name,
                "stream": "ext://sys.stdout",
                "filters": ["health_check"],
            },
        },
        "root": {
            "handlers": ["console"],
            "level": level,
        },
        "loggers": {
            # Align uvicorn access/error logs with app formatting
            "uvicorn.access": {
                "handlers": ["console_filtered"],
                "level": level,
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["console"],
                "level": level,
                "propagate": False,
            },
        },
    }


def configure_logging() -> None:
    """Configure structured logging for the FastAPI app."""
    env_level = os.getenv(LOG_LEVEL_ENV_VAR, DEFAULT_LOG_LEVEL)
    level = _coerce_log_level(env_level)

    dictConfig(_build_logging_config(level))
    logging.captureWarnings(True)

    # Ensure health check filter is applied to uvicorn.access
    # (uvicorn may reconfigure loggers, so we force it here too)
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.addFilter(HealthCheckFilter())

    for logger_name, preset_level in PRESET_LOGGER_LEVELS.items():
        logging.getLogger(logger_name).setLevel(preset_level)

    for noisy_logger in NOISY_LOGGERS:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    try:
        from loguru import logger as loguru_logger  # type: ignore

        # Drop binary frame hexdumps while keeping other loguru DEBUG lines (e.g. TTS traces)
        loguru_logger.remove()
        loguru_logger.add(
            sys.stderr, level="DEBUG", filter=lambda record: "BINARY" not in record["message"]
        )
    except Exception:
        pass

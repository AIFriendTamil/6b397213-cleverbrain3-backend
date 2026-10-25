import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

# Context variable to hold trace ID across async/sync execution contexts
trace_id_ctx: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)


def set_trace_id(trace_id: Optional[str]) -> None:
    """Set the trace ID for the current context."""
    trace_id_ctx.set(trace_id)


def get_trace_id() -> Optional[str]:
    """Get the trace ID from the current context."""
    return trace_id_ctx.get()


def clear_trace_id() -> None:
    """Clear the trace ID from the current context."""
    trace_id_ctx.set(None)


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter emitting log records as JSON objects."""

    def __init__(self, default_component: str = "backend") -> None:
        super().__init__()
        self.default_component = default_component

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()

        # Resolve component: record attribute > record.name > default_component
        component = getattr(record, "component", None) or getattr(
            record, "name", self.default_component
        )

        # Resolve trace_id: record attribute > contextvar > empty string
        trace_id = getattr(record, "trace_id", None) or get_trace_id() or ""

        log_data: Dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "message": record.getMessage(),
            "trace_id": trace_id,
            "component": component,
        }

        # Include exception information if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        if record.stack_info:
            log_data["stack_info"] = self.formatStack(record.stack_info)

        # Standard LogRecord fields to omit from extra payload
        standard_attrs = {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "component",
            "trace_id",
            "taskName",
        }

        # Collect any custom extra attributes attached to record
        for key, val in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                try:
                    json.dumps(val)
                    log_data[key] = val
                except (TypeError, OverflowError):
                    log_data[key] = str(val)

        return json.dumps(log_data)


class ComponentLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that binds component and trace_id into log records."""

    def __init__(
        self, logger: logging.Logger, component: str, extra: Optional[Dict[str, Any]] = None
    ) -> None:
        base_extra = extra or {}
        base_extra["component"] = component
        super().__init__(logger, base_extra)

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        extra = kwargs.get("extra", {})
        merged_extra = {**self.extra, **extra} if self.extra else extra
        kwargs["extra"] = merged_extra
        return msg, kwargs


def setup_logging(level: str = "INFO", component: str = "backend") -> None:
    """Configure root logger with JSONFormatter and stdout handler."""
    root_logger = logging.getLogger()
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root_logger.setLevel(numeric_level)

    # Clear existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter(default_component=component))
    root_logger.addHandler(handler)


def get_logger(
    name: str = "portal", component: Optional[str] = None
) -> Union[logging.Logger, ComponentLoggerAdapter]:
    """Get a logger or ComponentLoggerAdapter configured for structured logging."""
    logger = logging.getLogger(name)
    if component:
        return ComponentLoggerAdapter(logger, component=component)
    return logger

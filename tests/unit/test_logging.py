import json
import logging
import io
import sys
from src.lib.logging import (
    JSONFormatter,
    ComponentLoggerAdapter,
    set_trace_id,
    get_trace_id,
    clear_trace_id,
    setup_logging,
    get_logger,
)


def test_trace_id_context():
    clear_trace_id()
    assert get_trace_id() is None

    set_trace_id("trace-xyz-123")
    assert get_trace_id() == "trace-xyz-123"

    clear_trace_id()
    assert get_trace_id() is None


def test_json_formatter_basic():
    formatter = JSONFormatter(default_component="test_component")
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Hello %s",
        args=("world",),
        exc_info=None,
    )

    clear_trace_id()
    output = formatter.format(record)
    data = json.loads(output)

    assert "timestamp" in data
    assert data["level"] == "INFO"
    assert data["message"] == "Hello world"
    assert data["trace_id"] == ""
    assert data["component"] == "test_logger"


def test_json_formatter_trace_id_from_context():
    formatter = JSONFormatter()
    set_trace_id("trace-context-456")

    record = logging.LogRecord(
        name="api_logger",
        level=logging.WARNING,
        pathname="api.py",
        lineno=20,
        msg="Resource warning",
        args=(),
        exc_info=None,
    )

    output = formatter.format(record)
    data = json.loads(output)

    assert data["trace_id"] == "trace-context-456"
    assert data["level"] == "WARNING"
    assert data["message"] == "Resource warning"
    clear_trace_id()


def test_json_formatter_extra_fields_and_override():
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="worker",
        level=logging.ERROR,
        pathname="worker.py",
        lineno=30,
        msg="Failed process",
        args=(),
        exc_info=None,
    )
    record.component = "celery_worker"
    record.trace_id = "explicit-trace-789"
    record.job_id = "job_101"

    output = formatter.format(record)
    data = json.loads(output)

    assert data["component"] == "celery_worker"
    assert data["trace_id"] == "explicit-trace-789"
    assert data["job_id"] == "job_101"
    assert data["level"] == "ERROR"


def test_json_formatter_exception():
    formatter = JSONFormatter()
    try:
        raise ValueError("Invalid configuration payload")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="error_logger",
        level=logging.ERROR,
        pathname="config.py",
        lineno=40,
        msg="Exception occurred",
        args=(),
        exc_info=exc_info,
    )

    output = formatter.format(record)
    data = json.loads(output)

    assert data["level"] == "ERROR"
    assert "exception" in data
    assert "ValueError: Invalid configuration payload" in data["exception"]


def test_component_logger_adapter():
    logger = logging.getLogger("adapter_test")
    adapter = ComponentLoggerAdapter(logger, component="billing_service")

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    adapter.info("Invoice processed", extra={"invoice_id": "inv_99"})

    output = buffer.getvalue()
    data = json.loads(output.strip())

    assert data["component"] == "billing_service"
    assert data["message"] == "Invoice processed"
    assert data["invoice_id"] == "inv_99"

    logger.removeHandler(handler)


def test_get_logger_and_setup_logging():
    setup_logging(level="DEBUG", component="main_app")

    logger = get_logger("custom_service")
    assert isinstance(logger, logging.Logger)

    adapter_logger = get_logger("custom_service", component="custom_comp")
    assert isinstance(adapter_logger, ComponentLoggerAdapter)

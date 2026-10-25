"""Unit tests for ForecastingAdapter in src/connectors/forecasting/adapter.py."""

from datetime import datetime, timezone
import pytest
import httpx

from src.connectors.forecasting.adapter import AIForecastingAdapter, ForecastingAdapter
from src.services.deals.forecasting_port import (
    ForecastingAdapterUnavailableError,
    ForecastingError,
    ForecastingPort,
)


def test_adapter_inheritance():
    """Verify ForecastingAdapter inherits from ForecastingPort abstract base class."""
    adapter = ForecastingAdapter()
    assert isinstance(adapter, ForecastingPort)
    assert ForecastingAdapter is AIForecastingAdapter


def test_predict_timeline_empty_deal_id():
    """Verify error raised when empty deal_id provided."""
    adapter = ForecastingAdapter()
    with pytest.raises(ForecastingError, match="Deal ID must not be empty"):
        adapter.predict_timeline("")

    with pytest.raises(ForecastingError, match="Deal ID must not be empty"):
        adapter.predict_timeline("   ")


def test_predict_timeline_success():
    """Verify successful prediction handling when API returns valid response."""
    mock_payload = {
        "deal_id": "deal_001",
        "predicted_close_date": "2026-11-20T18:00:00+00:00",
        "confidence_score": 0.92,
        "model_version": "v2.1.0",
        "details": {"method": "xgboost_pipeline"},
        "milestone_estimates": [
            {
                "milestone_id": "ms_101",
                "milestone_title": "Security Assessment",
                "estimated_completion_date": "2026-11-05T12:00:00Z",
                "confidence_score": 0.95,
            }
        ],
    }

    def custom_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/forecast"
        assert "Authorization" in request.headers
        return httpx.Response(200, json=mock_payload)

    transport = httpx.MockTransport(custom_handler)
    client = httpx.Client(transport=transport)

    adapter = ForecastingAdapter(
        api_url="http://test-server/api/v1/forecast",
        api_key="test-key",
        http_client=client,
    )

    result = adapter.predict_timeline("deal_001", include_milestones=True)

    assert result.deal_id == "deal_001"
    assert result.predicted_close_date == datetime(2026, 11, 20, 18, 0, 0, tzinfo=timezone.utc)
    assert result.confidence_score == 0.92
    assert result.is_fallback is False
    assert result.model_version == "v2.1.0"
    assert result.details["method"] == "xgboost_pipeline"
    assert len(result.milestone_estimates) == 1

    ms = result.milestone_estimates[0]
    assert ms.milestone_id == "ms_101"
    assert ms.milestone_title == "Security Assessment"
    assert ms.estimated_completion_date == datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
    assert ms.confidence_score == 0.95


def test_predict_timeline_network_timeout():
    """Verify ForecastingAdapterUnavailableError raised when request times out."""

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Connection timed out", request=request)

    transport = httpx.MockTransport(timeout_handler)
    client = httpx.Client(transport=transport)

    adapter = ForecastingAdapter(
        api_url="http://test-server/api/v1/forecast",
        http_client=client,
    )

    with pytest.raises(ForecastingAdapterUnavailableError, match="connection timed out"):
        adapter.predict_timeline("deal_002")


def test_predict_timeline_network_request_error():
    """Verify ForecastingAdapterUnavailableError raised on connection errors."""

    def connection_error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Failed to resolve host", request=request)

    transport = httpx.MockTransport(connection_error_handler)
    client = httpx.Client(transport=transport)

    adapter = ForecastingAdapter(
        api_url="http://unreachable-host/api/v1/forecast",
        http_client=client,
    )

    with pytest.raises(ForecastingAdapterUnavailableError, match="service unreachable"):
        adapter.predict_timeline("deal_003")


def test_predict_timeline_server_error_503():
    """Verify 503 Service Unavailable raises ForecastingAdapterUnavailableError."""

    def server_error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "Service Unavailable"})

    transport = httpx.MockTransport(server_error_handler)
    client = httpx.Client(transport=transport)

    adapter = ForecastingAdapter(
        api_url="http://test-server/api/v1/forecast",
        http_client=client,
    )

    with pytest.raises(ForecastingAdapterUnavailableError, match="server status 503"):
        adapter.predict_timeline("deal_004")


def test_predict_timeline_client_error_400():
    """Verify 400 Bad Request raises ForecastingError."""

    def client_error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "Invalid deal input parameters"})

    transport = httpx.MockTransport(client_error_handler)
    client = httpx.Client(transport=transport)

    adapter = ForecastingAdapter(
        api_url="http://test-server/api/v1/forecast",
        http_client=client,
    )

    with pytest.raises(ForecastingError, match="returned error status 400"):
        adapter.predict_timeline("deal_005")


def test_predict_timeline_invalid_json_payload():
    """Verify missing predicted_close_date field raises ForecastingError."""

    def invalid_payload_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"deal_id": "deal_006", "confidence_score": 0.8})

    transport = httpx.MockTransport(invalid_payload_handler)
    client = httpx.Client(transport=transport)

    adapter = ForecastingAdapter(
        api_url="http://test-server/api/v1/forecast",
        http_client=client,
    )

    with pytest.raises(ForecastingError, match="missing 'predicted_close_date' field"):
        adapter.predict_timeline("deal_006")

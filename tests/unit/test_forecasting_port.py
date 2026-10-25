"""Unit tests for abstract domain forecasting port and dataclasses in forecasting_port.py."""

from datetime import datetime, timezone
import pytest

from src.services.deals.forecasting_port import (
    DealForecastResult,
    ForecastingAdapterUnavailableError,
    ForecastingError,
    ForecastingPort,
    MilestoneForecastEstimate,
)


def test_milestone_forecast_estimate_serialization():
    """Verify MilestoneForecastEstimate data structure and dictionary serialization."""
    target_dt = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)
    estimate = MilestoneForecastEstimate(
        milestone_id="ms_001",
        milestone_title="Legal Compliance Review",
        estimated_completion_date=target_dt,
        confidence_score=0.85,
    )

    assert estimate.milestone_id == "ms_001"
    assert estimate.milestone_title == "Legal Compliance Review"
    assert estimate.estimated_completion_date == target_dt
    assert estimate.confidence_score == 0.85

    res_dict = estimate.to_dict()
    assert res_dict["milestone_id"] == "ms_001"
    assert res_dict["milestone_title"] == "Legal Compliance Review"
    assert res_dict["estimated_completion_date"] == target_dt.isoformat()
    assert res_dict["confidence_score"] == 0.85


def test_deal_forecast_result_serialization():
    """Verify DealForecastResult data structure and dictionary serialization."""
    close_dt = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    milestone_dt = datetime(2026, 11, 30, 12, 0, 0, tzinfo=timezone.utc)

    milestone = MilestoneForecastEstimate(
        milestone_id="ms_002",
        milestone_title="Executive Signoff",
        estimated_completion_date=milestone_dt,
        confidence_score=0.9,
    )

    result = DealForecastResult(
        deal_id="deal_777",
        predicted_close_date=close_dt,
        confidence_score=0.88,
        milestone_estimates=[milestone],
        is_fallback=False,
        model_version="v1.2.0",
        details={"algorithm": "gradient_boosting"},
    )

    assert result.deal_id == "deal_777"
    assert result.is_fallback is False
    assert result.model_version == "v1.2.0"

    res_dict = result.to_dict()
    assert res_dict["deal_id"] == "deal_777"
    assert res_dict["predicted_close_date"] == close_dt.isoformat()
    assert res_dict["confidence_score"] == 0.88
    assert res_dict["is_fallback"] is False
    assert len(res_dict["milestone_estimates"]) == 1
    assert res_dict["milestone_estimates"][0]["milestone_id"] == "ms_002"
    assert res_dict["details"]["algorithm"] == "gradient_boosting"


def test_forecasting_exceptions():
    """Verify forecasting exception hierarchy."""
    err = ForecastingAdapterUnavailableError("AI engine timeout")
    assert isinstance(err, ForecastingError)
    assert str(err) == "AI engine timeout"


def test_forecasting_port_abstract_interface():
    """Verify that ForecastingPort cannot be instantiated directly without abstract method implementation."""
    with pytest.raises(TypeError):
        ForecastingPort()  # type: ignore[abstract]

    class DummyAdapter(ForecastingPort):
        def predict_timeline(
            self,
            deal_id: str,
            include_milestones: bool = True,
            **kwargs,
        ) -> DealForecastResult:
            close_dt = datetime(2026, 12, 1, 0, 0, 0, tzinfo=timezone.utc)
            return DealForecastResult(
                deal_id=deal_id,
                predicted_close_date=close_dt,
                confidence_score=0.95,
            )

    adapter = DummyAdapter()
    forecast = adapter.predict_timeline("deal_123")
    assert forecast.deal_id == "deal_123"
    assert forecast.confidence_score == 0.95

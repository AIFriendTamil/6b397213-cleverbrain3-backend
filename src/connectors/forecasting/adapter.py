"""Adapter connecting domain forecasting port to external AI forecasting engine or API."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from src.lib.config import settings
from src.services.deals.forecasting_port import (
    DealForecastResult,
    ForecastingAdapterUnavailableError,
    ForecastingError,
    ForecastingPort,
    MilestoneForecastEstimate,
)

logger = logging.getLogger(__name__)


class ForecastingAdapter(ForecastingPort):
    """Adapter connecting ForecastingPort to external or configured AI forecasting API."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: float = 5.0,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        """Initialize forecasting adapter with target API endpoint and credentials.

        Args:
            api_url: Base URL for external forecasting endpoint.
            api_key: Secret key or token for API authentication.
            timeout_seconds: Request timeout in seconds.
            http_client: Optional pre-configured httpx Client instance for requests.
        """
        self.api_url = (api_url or settings.FORECASTING_API_URL).rstrip("/")
        self.api_key = api_key or settings.FORECASTING_API_KEY
        self.timeout_seconds = timeout_seconds
        self._custom_client = http_client

    def predict_timeline(
        self,
        deal_id: str,
        include_milestones: bool = True,
        **kwargs: Any,
    ) -> DealForecastResult:
        """Predict expected close date and milestone completion timeline for a deal.

        Args:
            deal_id: Identifier of the target deal.
            include_milestones: Whether to include milestone completion date estimates.
            **kwargs: Additional contextual parameters passed to forecasting engine.

        Returns:
            DealForecastResult with predicted completion details.

        Raises:
            ForecastingAdapterUnavailableError: If external forecasting API is unreachable or times out.
            ForecastingError: If external engine returns invalid data or processing error.
        """
        if not deal_id or not deal_id.strip():
            raise ForecastingError("Deal ID must not be empty.")

        payload = {
            "deal_id": deal_id,
            "include_milestones": include_milestones,
            **kwargs,
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-API-Key"] = self.api_key

        try:
            if self._custom_client is not None:
                response = self._custom_client.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        self.api_url,
                        json=payload,
                        headers=headers,
                    )

            if response.status_code in (502, 503, 504, 408):
                raise ForecastingAdapterUnavailableError(
                    f"External forecasting API returned server status {response.status_code}"
                )

            response.raise_for_status()
            data = response.json()
            return self._parse_forecast_response(deal_id, data)

        except httpx.TimeoutException as exc:
            logger.warning("Forecasting API request timed out for deal %s: %s", deal_id, exc)
            raise ForecastingAdapterUnavailableError(
                f"Forecasting API connection timed out for deal '{deal_id}'"
            ) from exc

        except httpx.RequestError as exc:
            logger.warning("Forecasting API request failed for deal %s: %s", deal_id, exc)
            raise ForecastingAdapterUnavailableError(
                f"External forecasting service unreachable: {exc}"
            ) from exc

        except httpx.HTTPStatusError as exc:
            logger.error(
                "Forecasting API returned HTTP status %s for deal %s",
                exc.response.status_code,
                deal_id,
            )
            if exc.response.status_code >= 500:
                raise ForecastingAdapterUnavailableError(
                    f"External forecasting engine failed with HTTP status {exc.response.status_code}"
                ) from exc
            raise ForecastingError(
                f"External forecasting API returned error status {exc.response.status_code}"
            ) from exc

        except (ValueError, KeyError, TypeError) as exc:
            logger.error("Failed to parse forecast response payload for deal %s: %s", deal_id, exc)
            raise ForecastingError(
                f"Invalid forecasting response payload format for deal '{deal_id}'"
            ) from exc

    def _parse_forecast_response(self, deal_id: str, data: Dict[str, Any]) -> DealForecastResult:
        """Parse dictionary response from AI forecasting engine into DealForecastResult."""
        if not isinstance(data, dict):
            raise ForecastingError("Response payload must be a JSON object.")

        close_date_raw = data.get("predicted_close_date") or data.get("predicted_date")
        if not close_date_raw:
            raise ForecastingError("Response missing 'predicted_close_date' field.")

        predicted_close_dt = self._parse_datetime(close_date_raw)
        confidence_score = float(data.get("confidence_score", 0.5))
        model_version = data.get("model_version")
        details = data.get("details", {})
        if not isinstance(details, dict):
            details = {}

        milestone_estimates: List[MilestoneForecastEstimate] = []
        raw_milestones = data.get("milestone_estimates", [])
        if isinstance(raw_milestones, list):
            for ms in raw_milestones:
                if isinstance(ms, dict) and "milestone_id" in ms:
                    ms_date_raw = ms.get("estimated_completion_date") or ms.get("target_date")
                    if ms_date_raw:
                        ms_dt = self._parse_datetime(ms_date_raw)
                        ms_title = ms.get("milestone_title") or ms.get("title")
                        ms_score = float(ms.get("confidence_score", 1.0))
                        milestone_estimates.append(
                            MilestoneForecastEstimate(
                                milestone_id=str(ms["milestone_id"]),
                                milestone_title=ms_title,
                                estimated_completion_date=ms_dt,
                                confidence_score=ms_score,
                            )
                        )

        return DealForecastResult(
            deal_id=data.get("deal_id", deal_id),
            predicted_close_date=predicted_close_dt,
            confidence_score=confidence_score,
            milestone_estimates=milestone_estimates,
            is_fallback=False,
            model_version=model_version,
            details=details,
        )

    @staticmethod
    def _parse_datetime(dt_val: Any) -> datetime:
        """Helper to parse ISO string or datetime instance into UTC datetime."""
        if isinstance(dt_val, datetime):
            if dt_val.tzinfo is None:
                return dt_val.replace(tzinfo=timezone.utc)
            return dt_val

        if isinstance(dt_val, str):
            clean_str = dt_val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        raise ValueError(f"Cannot parse datetime from value: {dt_val}")


# Alias for backward compatibility / explicit naming
AIForecastingAdapter = ForecastingAdapter

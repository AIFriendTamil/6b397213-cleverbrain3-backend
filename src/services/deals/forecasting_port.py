"""Abstract domain interface port and data structures for deal timeline forecasting."""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional


class ForecastingError(Exception):
    """Base exception class for timeline forecasting errors."""

    pass


class ForecastingAdapterUnavailableError(ForecastingError):
    """Raised when external AI forecasting engine or model adapter is unavailable."""

    pass


@dataclass
class MilestoneForecastEstimate:
    """Estimated completion timeline for an individual deal milestone."""

    milestone_id: str
    milestone_title: Optional[str]
    estimated_completion_date: datetime
    confidence_score: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        """Convert milestone forecast estimate to dictionary format."""
        return {
            "milestone_id": self.milestone_id,
            "milestone_title": self.milestone_title,
            "estimated_completion_date": (
                self.estimated_completion_date.isoformat()
                if isinstance(self.estimated_completion_date, datetime)
                else str(self.estimated_completion_date)
            ),
            "confidence_score": self.confidence_score,
        }


@dataclass
class DealForecastResult:
    """Predictive timeline forecast result for a deal."""

    deal_id: str
    predicted_close_date: datetime
    confidence_score: float
    milestone_estimates: list[MilestoneForecastEstimate] = field(default_factory=list)
    is_fallback: bool = False
    model_version: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert forecast result instance to dictionary format."""
        return {
            "deal_id": self.deal_id,
            "predicted_close_date": (
                self.predicted_close_date.isoformat()
                if isinstance(self.predicted_close_date, datetime)
                else str(self.predicted_close_date)
            ),
            "confidence_score": self.confidence_score,
            "milestone_estimates": [m.to_dict() for m in self.milestone_estimates],
            "is_fallback": self.is_fallback,
            "model_version": self.model_version,
            "details": dict(self.details),
        }


class ForecastingPort(ABC):
    """Abstract domain interface port for deal timeline prediction and forecasting."""

    @abstractmethod
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
            DealForecastResult containing predicted close date and milestone estimates.

        Raises:
            ForecastingError: If forecasting processing fails.
            ForecastingAdapterUnavailableError: If external forecasting service is unreachable.
        """
        pass

"""Health check API endpoint route (delegates to src.api.health)."""

from src.api.health import HealthCheckResponse, health_check, router

__all__ = ["HealthCheckResponse", "health_check", "router"]

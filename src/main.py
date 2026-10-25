"""FastAPI application entrypoint module re-exporting the main application from src.api.main."""

from src.api.main import app, create_app, lifespan, register_signal_handlers

__all__ = ["app", "create_app", "lifespan", "register_signal_handlers"]

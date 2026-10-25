"""FastAPI application instantiation, lifespan management, and router configuration."""

import logging
import signal
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from src.api.acquisition import router as acquisition_router
from src.api.campaigns import router as campaigns_router
from src.api.clm_router import router as clm_router
from src.api.dashboard import router as dashboard_router
from src.api.dashboard_routes import router as financial_dashboard_router
from src.api.invoice_routes import router as invoice_router
from src.api.notification_routes import router as notification_router
from src.api.quote_routes import router as quote_router
from src.api.deals import router as deals_router
from src.api.funnel import router as funnel_router
from src.api.health import router as health_router
from src.api.knowledge_base import router as kb_router
from src.api.meetings import router as meetings_router
from src.api.reports import router as reports_router
from src.api.territory import router as territory_router
from src.api.tickets import router as tickets_router
from src.api.routes.auth import router as auth_router
from src.lib.config import settings
from src.lib.logging import clear_trace_id, set_trace_id, setup_logging
from src.models.base import Base, engine
from src.models.contract import (  # noqa: F401
    AmendmentAuditLog,
    ClauseHighlight,
    Contract,
    DocumentVersion,
    ESignatureRequest,
    RenewalNotificationLog,
    SLACommitment,
)
from src.models.campaign import Campaign  # noqa: F401
from src.models.funnel import FunnelStage  # noqa: F401
from src.models.meeting import MeetingBrief, ScheduledMeeting  # noqa: F401
from src.models.dashboard import (  # noqa: F401
    DashboardHistoricalSnapshot,
    DashboardMetricCache,
)
from src.models.deal import CriticalTask, Deal, DealStage, Milestone, Stakeholder  # noqa: F401
from src.models.lead import Lead, LeadSource  # noqa: F401
from src.models.session import Session  # noqa: F401
from src.models.tenant import Tenant  # noqa: F401
from src.models.user import User  # noqa: F401
from src.models.ticket import Ticket  # noqa: F401
from src.models.audit_log import TicketEvent  # noqa: F401
from src.models.csat import CSATResponse  # noqa: F401
from src.models.quote import Quote, QuoteApproval  # noqa: F401
from src.models.invoice import Invoice  # noqa: F401
from src.models.financial import ArAgingSnapshot, ArrMetric  # noqa: F401
from src.models.notification import Notification, NotificationPreference  # noqa: F401
from src.models.knowledge_base import (  # noqa: F401
    KBBookmark,
    KBCategory,
    KBResource,
    KBTutorialStep,
    KBUserProgress,
)

logger = logging.getLogger("backend.api")


def register_signal_handlers() -> None:
    """Register system signal handlers for SIGTERM and SIGINT for graceful termination logging."""

    def _signal_handler(sig: int, frame: Any) -> None:
        logger.info("Received signal %s, preparing for graceful shutdown...", sig)

    try:
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)
    except (ValueError, OSError):
        # Signal handler registration may be restricted in non-main threads or test environments
        pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI application lifespan context manager for startup and shutdown routines."""
    # 1. Configure structured JSON logging
    setup_logging(
        level="DEBUG" if settings.DEBUG else "INFO",
        component="api",
    )
    logger.info("Starting up Portal Platform API...")

    # 2. Register signal handlers
    register_signal_handlers()

    # 3. Startup schema creation (create all database tables)
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database schema initialized successfully.")
    except Exception as err:
        logger.error("Failed to initialize database schema on startup: %s", err)
        raise

    yield

    # Shutdown routines
    logger.info("Shutting down Portal Platform API...")
    try:
        engine.dispose()
        logger.info("Database connection engine disposed successfully.")
    except Exception as err:
        logger.error("Error disposing database connection engine: %s", err)


def create_app() -> FastAPI:
    """Instantiate and configure the FastAPI application instance."""
    app_instance = FastAPI(
        title="Portal Platform API",
        description="User and Tenant Portal Foundation Backend API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Configure CORS middleware
    app_instance.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Middleware for request trace ID tracking and structured log context
    @app_instance.middleware("http")
    async def trace_id_middleware(request: Request, call_next: Callable) -> Response:
        trace_id = (
            request.headers.get("X-Trace-ID")
            or request.headers.get("X-Request-ID")
            or str(uuid.uuid4())
        )
        set_trace_id(trace_id)
        try:
            response = await call_next(request)
            response.headers["X-Trace-ID"] = trace_id
            return response
        finally:
            clear_trace_id()

    # Include endpoint routers
    app_instance.include_router(auth_router)
    app_instance.include_router(health_router)
    app_instance.include_router(dashboard_router)
    app_instance.include_router(financial_dashboard_router)
    app_instance.include_router(quote_router)
    app_instance.include_router(invoice_router)
    app_instance.include_router(notification_router)
    app_instance.include_router(clm_router)
    app_instance.include_router(deals_router)
    app_instance.include_router(acquisition_router)
    app_instance.include_router(funnel_router)
    app_instance.include_router(territory_router)
    app_instance.include_router(meetings_router)
    app_instance.include_router(campaigns_router)
    app_instance.include_router(tickets_router)
    app_instance.include_router(reports_router)
    app_instance.include_router(kb_router)

    return app_instance


# Application singleton instance
app = create_app()

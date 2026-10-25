from functools import lru_cache
from typing import List, Union

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmailGatewayConfig(BaseModel):
    """Schema for Email Notification Gateway configuration."""

    provider: str = "mock"
    api_url: str = "http://localhost:8003/api/v1/email"
    api_key: str = "dev-email-gateway-api-key"
    sender_address: str = "notifications@portal.example.com"
    timeout_seconds: int = 5


class SMSGatewayConfig(BaseModel):
    """Schema for SMS Notification Gateway configuration."""

    provider: str = "mock"
    api_url: str = "http://localhost:8004/api/v1/sms"
    api_key: str = "dev-sms-gateway-api-key"
    sender_number: str = "+15550199"
    timeout_seconds: int = 5


class VideoProviderConfig(BaseModel):
    """Schema for Video Provider configuration."""

    provider: str = "mock"
    api_url: str = "http://localhost:8005/api/v1/video"
    api_key: str = "dev-video-provider-api-key"
    embed_base_url: str = "https://player.vimeo.com/video"
    timeout_seconds: int = 5


class Settings(BaseSettings):
    """Application configuration and environment variable manager."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Environment and core settings
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Logging configuration
    LOG_LEVEL: str = "INFO"
    LOG_COMPONENT: str = "backend"

    # Operational secrets
    SECRET_KEY: str = "dev-secret-key-change-in-production-use-strong-key"

    # Persistence connection URLs
    DATABASE_URL: str = "sqlite:////workspace/backend/src/lib/portal.db"
    REDIS_URL: str = "redis://localhost:6379/0"

    # Session & Cookie configuration
    SESSION_COOKIE_NAME: str = "session_id"
    SESSION_EXPIRE_HOURS: int = 24
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"

    # Cross-Origin Resource Sharing
    CORS_ORIGINS: Union[List[str], str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # Dashboard configuration & feature flags
    DASHBOARD_CACHE_TTL_SECONDS: int = 300
    DASHBOARD_REFRESH_INTERVAL_SECONDS: int = 60

    # Domain feature flags
    FEATURE_FLAG_SALES_DOMAIN: bool = True
    FEATURE_FLAG_LEADS_DOMAIN: bool = True
    FEATURE_FLAG_SUPPORT_DOMAIN: bool = True
    FEATURE_FLAG_FINANCE_DOMAIN: bool = True

    # Contract Lifecycle Management (CLM) configuration
    CLM_STORAGE_PATH: str = "/workspace/backend/storage/documents"
    CLM_MAX_FILE_SIZE_BYTES: int = 26214400  # 25 MB default
    CLM_ALLOWED_EXTENSIONS: Union[List[str], str] = [".pdf", ".docx"]
    CLM_STORAGE_ENCRYPTION_KEY: str = "dev-clm-storage-encryption-key-32b"
    CLM_STORAGE_ENCRYPTION_ENABLED: bool = True
    CLM_STORAGE_ENCRYPTION_ALGORITHM: str = "AES-256"
    CLM_HMAC_SECRET_KEY: str = "dev-clm-hmac-secret-key-change-in-prod"
    CLM_ESIGN_WEBHOOK_SECRET: str = "dev-clm-esign-webhook-secret"

    # Deal Pipeline & Forecasting configuration
    FORECASTING_API_URL: str = "http://localhost:8001/api/v1/forecast"
    FORECASTING_API_KEY: str = "dev-forecasting-api-key"
    CRITICAL_TASK_OVERDUE_THRESHOLD_HOURS: int = 24

    # Lead Acquisition, Funnel Analytics, and Territory Reporting configuration
    ROI_TARGET_THRESHOLD_PERCENTAGE: float = 100.0
    FUNNEL_BOTTLENECK_TARGET_CONVERSION_RATE: float = 20.0
    DEFAULT_UNMAPPED_REGION_NAME: str = "Unmapped Region"
    CALENDAR_PROVIDER: str = "mock"
    CALENDAR_FALLBACK_TO_LOCAL: bool = True
    CALENDAR_API_URL: str = "http://localhost:8002/api/v1/calendar"
    CALENDAR_API_KEY: str = "dev-calendar-api-key"
    CALENDAR_API_TIMEOUT_SECONDS: int = 5

    # Support Escalation & SLA Management configuration
    SLA_P1_RESPONSE_HOURS: float = 1.0
    SLA_P1_RESOLUTION_HOURS: float = 4.0
    SLA_P1_WARNING_THRESHOLD_PERCENT: float = 75.0

    SLA_P2_RESPONSE_HOURS: float = 4.0
    SLA_P2_RESOLUTION_HOURS: float = 24.0
    SLA_P2_WARNING_THRESHOLD_PERCENT: float = 80.0

    SLA_P3_RESPONSE_HOURS: float = 24.0
    SLA_P3_RESOLUTION_HOURS: float = 72.0
    SLA_P3_WARNING_THRESHOLD_PERCENT: float = 80.0

    TICKET_ATTACHMENT_MAX_SIZE_BYTES: int = 26214400  # 25 MB default
    TICKET_ATTACHMENT_ALLOWED_EXTENSIONS: Union[List[str], str] = [
        ".pdf",
        ".docx",
        ".png",
        ".jpg",
        ".jpeg",
        ".txt",
        ".csv",
    ]
    TICKET_ATTACHMENT_STORAGE_PATH: str = "/workspace/backend/storage/attachments"
    TICKET_ATTACHMENT_STORAGE_PROVIDER: str = "local"

    # Financial Operations Transparency & Notification Gateway configuration
    FINANCIAL_DATA_ENCRYPTION_KEY: str = "dev-financial-data-encryption-key-32b"
    FINANCIAL_DATA_ENCRYPTION_ENABLED: bool = True
    FINANCIAL_DATA_ENCRYPTION_ALGORITHM: str = "AES-256"
    FINANCIAL_DEFAULT_CURRENCY: str = "USD"

    EMAIL_GATEWAY_PROVIDER: str = "mock"
    EMAIL_GATEWAY_API_URL: str = "http://localhost:8003/api/v1/email"
    EMAIL_GATEWAY_API_KEY: str = "dev-email-gateway-api-key"
    EMAIL_GATEWAY_SENDER_ADDRESS: str = "notifications@portal.example.com"
    EMAIL_GATEWAY_TIMEOUT_SECONDS: int = 5

    SMS_GATEWAY_PROVIDER: str = "mock"
    SMS_GATEWAY_API_URL: str = "http://localhost:8004/api/v1/sms"
    SMS_GATEWAY_API_KEY: str = "dev-sms-gateway-api-key"
    SMS_GATEWAY_SENDER_NUMBER: str = "+15550199"
    SMS_GATEWAY_TIMEOUT_SECONDS: int = 5

    NOTIFICATION_MAX_RETRIES: int = 3
    NOTIFICATION_RETRY_BACKOFF_SECONDS: int = 5
    NOTIFICATION_IN_APP_RETENTION_DAYS: int = 30

    # Knowledge Base search pagination and video provider configuration
    KB_SEARCH_DEFAULT_PAGE: int = 1
    KB_SEARCH_DEFAULT_LIMIT: int = 10
    KB_SEARCH_MAX_LIMIT: int = 100

    VIDEO_PROVIDER: str = "mock"
    VIDEO_PROVIDER_API_URL: str = "http://localhost:8005/api/v1/video"
    VIDEO_PROVIDER_API_KEY: str = "dev-video-provider-api-key"
    VIDEO_PROVIDER_EMBED_BASE_URL: str = "https://player.vimeo.com/video"
    VIDEO_PROVIDER_TIMEOUT_SECONDS: int = 5

    @property
    def email_gateway_config(self) -> EmailGatewayConfig:
        return EmailGatewayConfig(
            provider=self.EMAIL_GATEWAY_PROVIDER,
            api_url=self.EMAIL_GATEWAY_API_URL,
            api_key=self.EMAIL_GATEWAY_API_KEY,
            sender_address=self.EMAIL_GATEWAY_SENDER_ADDRESS,
            timeout_seconds=self.EMAIL_GATEWAY_TIMEOUT_SECONDS,
        )

    @property
    def sms_gateway_config(self) -> SMSGatewayConfig:
        return SMSGatewayConfig(
            provider=self.SMS_GATEWAY_PROVIDER,
            api_url=self.SMS_GATEWAY_API_URL,
            api_key=self.SMS_GATEWAY_API_KEY,
            sender_number=self.SMS_GATEWAY_SENDER_NUMBER,
            timeout_seconds=self.SMS_GATEWAY_TIMEOUT_SECONDS,
        )

    @property
    def video_provider_config(self) -> VideoProviderConfig:
        return VideoProviderConfig(
            provider=self.VIDEO_PROVIDER,
            api_url=self.VIDEO_PROVIDER_API_URL,
            api_key=self.VIDEO_PROVIDER_API_KEY,
            embed_base_url=self.VIDEO_PROVIDER_EMBED_BASE_URL,
            timeout_seconds=self.VIDEO_PROVIDER_TIMEOUT_SECONDS,
        )

    @property
    def enabled_domains(self) -> List[str]:
        domains = []
        if self.FEATURE_FLAG_SALES_DOMAIN:
            domains.append("sales")
        if self.FEATURE_FLAG_LEADS_DOMAIN:
            domains.append("leads")
        if self.FEATURE_FLAG_SUPPORT_DOMAIN:
            domains.append("support")
        if self.FEATURE_FLAG_FINANCE_DOMAIN:
            domains.append("finance")
        return domains

    @field_validator(
        "CORS_ORIGINS",
        "CLM_ALLOWED_EXTENSIONS",
        "TICKET_ATTACHMENT_ALLOWED_EXTENSIONS",
        mode="before",
    )
    @classmethod
    def assemble_list_from_str(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings instance."""
    return Settings()


# Singleton instance for direct import
settings = get_settings()

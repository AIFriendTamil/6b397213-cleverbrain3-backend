import os
from src.lib.config import (
    EmailGatewayConfig,
    SMSGatewayConfig,
    VideoProviderConfig,
    Settings,
    get_settings,
)


def test_default_settings():
    settings = Settings()
    assert settings.ENVIRONMENT == "development"
    assert settings.DEBUG is True
    assert settings.SECRET_KEY == "dev-secret-key-change-in-production-use-strong-key"
    assert settings.DATABASE_URL == "sqlite:////workspace/backend/src/lib/portal.db"
    assert settings.REDIS_URL == "redis://localhost:6379/0"
    assert settings.SESSION_COOKIE_NAME == "session_id"
    assert settings.DASHBOARD_CACHE_TTL_SECONDS == 300
    assert settings.DASHBOARD_REFRESH_INTERVAL_SECONDS == 60
    assert settings.FEATURE_FLAG_SALES_DOMAIN is True
    assert settings.FEATURE_FLAG_LEADS_DOMAIN is True
    assert settings.FEATURE_FLAG_SUPPORT_DOMAIN is True
    assert settings.FEATURE_FLAG_FINANCE_DOMAIN is True
    assert settings.enabled_domains == ["sales", "leads", "support", "finance"]
    # CLM defaults
    assert settings.CLM_STORAGE_PATH == "/workspace/backend/storage/documents"
    assert settings.CLM_MAX_FILE_SIZE_BYTES == 26214400
    assert settings.CLM_ALLOWED_EXTENSIONS == [".pdf", ".docx"]
    assert settings.CLM_STORAGE_ENCRYPTION_KEY == "dev-clm-storage-encryption-key-32b"
    assert settings.CLM_STORAGE_ENCRYPTION_ENABLED is True
    assert settings.CLM_STORAGE_ENCRYPTION_ALGORITHM == "AES-256"
    assert settings.CLM_HMAC_SECRET_KEY == "dev-clm-hmac-secret-key-change-in-prod"
    assert settings.CLM_ESIGN_WEBHOOK_SECRET == "dev-clm-esign-webhook-secret"
    # Forecasting and threshold defaults
    assert settings.FORECASTING_API_URL == "http://localhost:8001/api/v1/forecast"
    assert settings.FORECASTING_API_KEY == "dev-forecasting-api-key"
    assert settings.CRITICAL_TASK_OVERDUE_THRESHOLD_HOURS == 24
    # SLA defaults
    assert settings.SLA_P1_RESPONSE_HOURS == 1.0
    assert settings.SLA_P1_RESOLUTION_HOURS == 4.0
    assert settings.SLA_P1_WARNING_THRESHOLD_PERCENT == 75.0
    assert settings.SLA_P2_RESPONSE_HOURS == 4.0
    assert settings.SLA_P2_RESOLUTION_HOURS == 24.0
    assert settings.SLA_P2_WARNING_THRESHOLD_PERCENT == 80.0
    assert settings.SLA_P3_RESPONSE_HOURS == 24.0
    assert settings.SLA_P3_RESOLUTION_HOURS == 72.0
    assert settings.SLA_P3_WARNING_THRESHOLD_PERCENT == 80.0
    # Ticket attachment defaults
    assert settings.TICKET_ATTACHMENT_MAX_SIZE_BYTES == 26214400
    assert settings.TICKET_ATTACHMENT_ALLOWED_EXTENSIONS == [
        ".pdf",
        ".docx",
        ".png",
        ".jpg",
        ".jpeg",
        ".txt",
        ".csv",
    ]
    assert (
        settings.TICKET_ATTACHMENT_STORAGE_PATH
        == "/workspace/backend/storage/attachments"
    )
    assert settings.TICKET_ATTACHMENT_STORAGE_PROVIDER == "local"
    # Financial Operations & Notification Gateway defaults
    assert settings.FINANCIAL_DATA_ENCRYPTION_KEY == "dev-financial-data-encryption-key-32b"
    assert settings.FINANCIAL_DATA_ENCRYPTION_ENABLED is True
    assert settings.FINANCIAL_DATA_ENCRYPTION_ALGORITHM == "AES-256"
    assert settings.FINANCIAL_DEFAULT_CURRENCY == "USD"
    assert settings.EMAIL_GATEWAY_PROVIDER == "mock"
    assert settings.EMAIL_GATEWAY_API_URL == "http://localhost:8003/api/v1/email"
    assert settings.EMAIL_GATEWAY_API_KEY == "dev-email-gateway-api-key"
    assert settings.EMAIL_GATEWAY_SENDER_ADDRESS == "notifications@portal.example.com"
    assert settings.EMAIL_GATEWAY_TIMEOUT_SECONDS == 5
    assert settings.SMS_GATEWAY_PROVIDER == "mock"
    assert settings.SMS_GATEWAY_API_URL == "http://localhost:8004/api/v1/sms"
    assert settings.SMS_GATEWAY_API_KEY == "dev-sms-gateway-api-key"
    assert settings.SMS_GATEWAY_SENDER_NUMBER == "+15550199"
    assert settings.SMS_GATEWAY_TIMEOUT_SECONDS == 5
    assert settings.NOTIFICATION_MAX_RETRIES == 3
    assert settings.NOTIFICATION_RETRY_BACKOFF_SECONDS == 5
    assert settings.NOTIFICATION_IN_APP_RETENTION_DAYS == 30

    email_cfg = settings.email_gateway_config
    assert isinstance(email_cfg, EmailGatewayConfig)
    assert email_cfg.provider == "mock"
    assert email_cfg.sender_address == "notifications@portal.example.com"

    sms_cfg = settings.sms_gateway_config
    assert isinstance(sms_cfg, SMSGatewayConfig)
    assert sms_cfg.provider == "mock"
    assert sms_cfg.sender_number == "+15550199"

    # KB and Video Provider defaults
    assert settings.KB_SEARCH_DEFAULT_PAGE == 1
    assert settings.KB_SEARCH_DEFAULT_LIMIT == 10
    assert settings.KB_SEARCH_MAX_LIMIT == 100
    assert settings.VIDEO_PROVIDER == "mock"
    assert settings.VIDEO_PROVIDER_API_URL == "http://localhost:8005/api/v1/video"
    assert settings.VIDEO_PROVIDER_API_KEY == "dev-video-provider-api-key"
    assert settings.VIDEO_PROVIDER_EMBED_BASE_URL == "https://player.vimeo.com/video"
    assert settings.VIDEO_PROVIDER_TIMEOUT_SECONDS == 5

    video_cfg = settings.video_provider_config
    assert isinstance(video_cfg, VideoProviderConfig)
    assert video_cfg.provider == "mock"
    assert video_cfg.embed_base_url == "https://player.vimeo.com/video"


def test_env_override(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "custom-secret-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/dbname")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("CORS_ORIGINS", "http://example.com,http://localhost:3000")
    monkeypatch.setenv("DASHBOARD_CACHE_TTL_SECONDS", "600")
    monkeypatch.setenv("DASHBOARD_REFRESH_INTERVAL_SECONDS", "120")
    monkeypatch.setenv("FEATURE_FLAG_SUPPORT_DOMAIN", "false")
    monkeypatch.setenv("CLM_STORAGE_PATH", "/tmp/clm_docs")
    monkeypatch.setenv("CLM_MAX_FILE_SIZE_BYTES", "10485760")
    monkeypatch.setenv("CLM_ALLOWED_EXTENSIONS", ".pdf,.docx,.txt")
    monkeypatch.setenv("CLM_STORAGE_ENCRYPTION_KEY", "custom-encryption-key")
    monkeypatch.setenv("CLM_STORAGE_ENCRYPTION_ENABLED", "false")
    monkeypatch.setenv("CLM_HMAC_SECRET_KEY", "custom-hmac-secret")
    monkeypatch.setenv("CLM_ESIGN_WEBHOOK_SECRET", "custom-webhook-secret")
    monkeypatch.setenv("FORECASTING_API_URL", "https://api.forecasting.example.com/v1/predict")
    monkeypatch.setenv("FORECASTING_API_KEY", "custom-forecasting-key")
    monkeypatch.setenv("CRITICAL_TASK_OVERDUE_THRESHOLD_HOURS", "48")
    monkeypatch.setenv("SLA_P1_RESPONSE_HOURS", "0.5")
    monkeypatch.setenv("SLA_P1_RESOLUTION_HOURS", "2.0")
    monkeypatch.setenv("SLA_P1_WARNING_THRESHOLD_PERCENT", "70.0")
    monkeypatch.setenv("TICKET_ATTACHMENT_MAX_SIZE_BYTES", "10485760")
    monkeypatch.setenv("TICKET_ATTACHMENT_ALLOWED_EXTENSIONS", ".png,.jpg")
    monkeypatch.setenv("TICKET_ATTACHMENT_STORAGE_PATH", "/tmp/attachments")
    monkeypatch.setenv("TICKET_ATTACHMENT_STORAGE_PROVIDER", "s3")
    monkeypatch.setenv("EMAIL_GATEWAY_PROVIDER", "sendgrid")
    monkeypatch.setenv("EMAIL_GATEWAY_API_URL", "https://api.sendgrid.com/v3/mail/send")
    monkeypatch.setenv("SMS_GATEWAY_PROVIDER", "twilio")
    monkeypatch.setenv("SMS_GATEWAY_SENDER_NUMBER", "+18005550199")
    monkeypatch.setenv("KB_SEARCH_DEFAULT_LIMIT", "25")
    monkeypatch.setenv("VIDEO_PROVIDER", "vimeo")
    monkeypatch.setenv("VIDEO_PROVIDER_API_KEY", "custom-video-key")

    settings = Settings()
    assert settings.SECRET_KEY == "custom-secret-key"
    assert settings.DATABASE_URL == "postgresql://user:pass@localhost:5432/dbname"
    assert settings.REDIS_URL == "redis://localhost:6379/1"
    assert settings.CORS_ORIGINS == ["http://example.com", "http://localhost:3000"]
    assert settings.DASHBOARD_CACHE_TTL_SECONDS == 600
    assert settings.DASHBOARD_REFRESH_INTERVAL_SECONDS == 120
    assert settings.FEATURE_FLAG_SUPPORT_DOMAIN is False
    assert settings.enabled_domains == ["sales", "leads", "finance"]
    assert settings.CLM_STORAGE_PATH == "/tmp/clm_docs"
    assert settings.CLM_MAX_FILE_SIZE_BYTES == 10485760
    assert settings.CLM_ALLOWED_EXTENSIONS == [".pdf", ".docx", ".txt"]
    assert settings.CLM_STORAGE_ENCRYPTION_KEY == "custom-encryption-key"
    assert settings.CLM_STORAGE_ENCRYPTION_ENABLED is False
    assert settings.CLM_HMAC_SECRET_KEY == "custom-hmac-secret"
    assert settings.CLM_ESIGN_WEBHOOK_SECRET == "custom-webhook-secret"
    assert settings.FORECASTING_API_URL == "https://api.forecasting.example.com/v1/predict"
    assert settings.FORECASTING_API_KEY == "custom-forecasting-key"
    assert settings.CRITICAL_TASK_OVERDUE_THRESHOLD_HOURS == 48
    assert settings.SLA_P1_RESPONSE_HOURS == 0.5
    assert settings.SLA_P1_RESOLUTION_HOURS == 2.0
    assert settings.SLA_P1_WARNING_THRESHOLD_PERCENT == 70.0
    assert settings.TICKET_ATTACHMENT_MAX_SIZE_BYTES == 10485760
    assert settings.TICKET_ATTACHMENT_ALLOWED_EXTENSIONS == [".png", ".jpg"]
    assert settings.TICKET_ATTACHMENT_STORAGE_PATH == "/tmp/attachments"
    assert settings.TICKET_ATTACHMENT_STORAGE_PROVIDER == "s3"
    assert settings.EMAIL_GATEWAY_PROVIDER == "sendgrid"
    assert settings.EMAIL_GATEWAY_API_URL == "https://api.sendgrid.com/v3/mail/send"
    assert settings.SMS_GATEWAY_PROVIDER == "twilio"
    assert settings.SMS_GATEWAY_SENDER_NUMBER == "+18005550199"

    email_cfg = settings.email_gateway_config
    assert email_cfg.provider == "sendgrid"
    assert email_cfg.api_url == "https://api.sendgrid.com/v3/mail/send"

    sms_cfg = settings.sms_gateway_config
    assert sms_cfg.provider == "twilio"
    assert sms_cfg.sender_number == "+18005550199"

    assert settings.KB_SEARCH_DEFAULT_LIMIT == 25
    video_cfg = settings.video_provider_config
    assert video_cfg.provider == "vimeo"
    assert video_cfg.api_key == "custom-video-key"


def test_get_settings_cached():
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2

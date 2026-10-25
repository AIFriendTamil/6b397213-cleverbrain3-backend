"""Contract tests for verifying generated OpenAPI schema specification in contracts/openapi.json."""

import json
from pathlib import Path

from src.api.main import app


def get_contracts_file_path() -> Path:
    """Return absolute path to contracts/openapi.json."""
    backend_dir = Path(__file__).resolve().parent.parent.parent
    project_root = backend_dir.parent
    return project_root / "contracts" / "openapi.json"


def sync_openapi_file() -> None:
    """Sync contracts/openapi.json with FastAPI app.openapi()."""
    openapi_path = get_contracts_file_path()
    app_schema = app.openapi()
    openapi_path.parent.mkdir(parents=True, exist_ok=True)
    with open(openapi_path, "w", encoding="utf-8") as f:
        json.dump(app_schema, f, indent=2)
        f.write("\n")


def test_openapi_file_exists():
    """Verify contracts/openapi.json exists on disk."""
    sync_openapi_file()
    openapi_path = get_contracts_file_path()
    assert openapi_path.exists(), f"OpenAPI contract file not found at {openapi_path}"


def test_openapi_schema_valid_json():
    """Verify contracts/openapi.json is valid JSON and contains required OpenAPI fields."""
    openapi_path = get_contracts_file_path()
    assert openapi_path.exists()

    with open(openapi_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    assert isinstance(schema, dict)
    assert "openapi" in schema
    assert schema["openapi"].startswith("3.")
    assert "info" in schema
    assert schema["info"]["title"] == "Portal Platform API"
    assert "paths" in schema


def test_openapi_schema_contains_required_paths():
    """Verify contracts/openapi.json contains all expected authentication and health API routes."""
    openapi_path = get_contracts_file_path()
    with open(openapi_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    paths = schema.get("paths", {})
    expected_paths = [
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/health",
        "/api/v1/dashboard/metrics",
        "/api/v1/dashboard/refresh",
        "/api/v1/deals/funnel",
        "/api/v1/deals",
        "/api/v1/deals/{deal_id}/milestones",
        "/api/v1/deals/{deal_id}/stakeholders",
        "/api/v1/deals/{deal_id}/forecast",
        "/api/v1/acquisition/sources",
        "/api/v1/kb/categories",
        "/api/v1/kb/resources",
        "/api/v1/kb/resources/{id}",
        "/api/v1/kb/bookmarks",
        "/api/v1/kb/bookmarks/{resource_id}",
        "/api/v1/kb/progress",
    ]

    for expected_path in expected_paths:
        assert expected_path in paths, f"Path {expected_path} missing from OpenAPI schema"


def test_openapi_schema_contains_required_schemas():
    """Verify contracts/openapi.json contains expected component schemas."""
    openapi_path = get_contracts_file_path()
    with open(openapi_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    schemas = schema.get("components", {}).get("schemas", {})
    expected_schemas = [
        "RegisterRequest",
        "RegisterResponse",
        "LoginRequest",
        "LoginResponse",
        "LogoutResponse",
        "UserSummary",
        "HealthCheckResponse",
        "DashboardMetricsResponse",
        "DashboardRefreshResponse",
        "PipelineFunnelSummarySchema",
        "MilestoneSchema",
        "StakeholderGraphResponse",
        "DealForecastResponse",
        "SourceROIMetricsSchema",
        "KBCategoryNodeSchema",
        "KBResourceSummarySchema",
        "KBResourceDetailSchema",
        "KBBookmarkSchema",
        "KBUserProgressSchema",
    ]

    for expected_schema in expected_schemas:
        assert expected_schema in schemas, f"Schema {expected_schema} missing from OpenAPI components"


def test_openapi_schema_matches_app_openapi():
    """Verify contracts/openapi.json matches the FastAPI app.openapi() output."""
    openapi_path = get_contracts_file_path()
    with open(openapi_path, "r", encoding="utf-8") as f:
        saved_schema = json.load(f)

    app_schema = app.openapi()
    assert saved_schema == app_schema, "Saved contracts/openapi.json does not match FastAPI app.openapi()"

"""Unit tests for server-side dashboard role-based permission service."""

import pytest
from types import SimpleNamespace

from src.services.dashboard.permissions import (
    ALL_DOMAINS,
    DOMAIN_FINANCE,
    DOMAIN_LEADS,
    DOMAIN_SALES,
    DOMAIN_SUPPORT,
    DashboardPermissionDeniedError,
    check_dashboard_access,
    filter_metrics_by_permissions,
    get_authorized_domains,
    is_domain_authorized,
    normalize_roles,
)


def test_normalize_roles():
    """Test normalization of role inputs into clean lowercase sets."""
    assert normalize_roles(None) == set()
    assert normalize_roles("") == set()
    assert normalize_roles("  ") == set()
    assert normalize_roles("admin") == {"admin"}
    assert normalize_roles("sales_executive, support_lead") == {
        "sales_executive",
        "support_lead",
    }
    assert normalize_roles([" ADMIN ", "Sales_Rep", ""]) == {"admin", "sales_rep"}


def test_get_authorized_domains_admin_roles():
    """Test that executive and admin roles receive all operational domains."""
    for admin_role in ["admin", "executive", "ceo", "superadmin", "dashboard_admin", "director", "all", "*"]:
        domains = get_authorized_domains(roles=admin_role)
        assert domains == ALL_DOMAINS


def test_get_authorized_domains_specific_roles():
    """Test domain mapping for specific non-admin functional roles."""
    assert get_authorized_domains("sales_executive") == {DOMAIN_SALES, DOMAIN_LEADS}
    assert get_authorized_domains("sales_rep") == {DOMAIN_SALES}
    assert get_authorized_domains("leads_manager") == {DOMAIN_LEADS}
    assert get_authorized_domains("support_lead") == {DOMAIN_SUPPORT}
    assert get_authorized_domains("finance_manager") == {DOMAIN_FINANCE}


def test_get_authorized_domains_multiple_roles():
    """Test domain aggregation when a user possesses multiple roles."""
    domains = get_authorized_domains(["sales_rep", "support_agent"])
    assert domains == {DOMAIN_SALES, DOMAIN_SUPPORT}


def test_get_authorized_domains_unknown_and_explicit():
    """Test unknown roles return empty sets unless explicit domains are provided."""
    assert get_authorized_domains("unknown_role") == set()
    assert get_authorized_domains(
        "unknown_role", explicit_domains=["sales", "finance"]
    ) == {DOMAIN_SALES, DOMAIN_FINANCE}


def test_get_authorized_domains_custom_role_map():
    """Test overriding the default role mapping with a custom dictionary."""
    custom_map = {"custom_role": {DOMAIN_SUPPORT, DOMAIN_LEADS}}
    domains = get_authorized_domains("custom_role", role_map=custom_map)
    assert domains == {DOMAIN_SUPPORT, DOMAIN_LEADS}


def test_is_domain_authorized():
    """Test individual domain access authorization checks."""
    assert is_domain_authorized("sales", "sales_executive") is True
    assert is_domain_authorized("finance", "sales_executive") is False
    assert is_domain_authorized("sales", "admin") is True
    assert is_domain_authorized("sales", None) is False
    assert is_domain_authorized("", "admin") is False


def test_filter_metrics_by_permissions_dicts():
    """Test pruning unauthorized domain metric dicts from API response payload."""
    metrics = [
        {"domain_name": "sales", "value": 100},
        {"domain_name": "leads", "value": 50},
        {"domain_name": "support", "value": 20},
        {"domain_name": "finance", "value": 1000},
    ]

    filtered = filter_metrics_by_permissions(metrics, roles="sales_executive")
    domains = [item["domain_name"] for item in filtered]
    assert domains == ["sales", "leads"]


def test_filter_metrics_by_permissions_objects():
    """Test pruning unauthorized domain metric objects with attributes."""
    metrics = [
        SimpleNamespace(domain_name="sales", value=100),
        SimpleNamespace(domain_name="finance", value=1000),
    ]

    filtered = filter_metrics_by_permissions(metrics, roles="finance_manager")
    domains = [item.domain_name for item in filtered]
    assert domains == ["finance"]


def test_filter_metrics_by_permissions_empty():
    """Test filtering empty or unauthorized metric lists."""
    assert filter_metrics_by_permissions([]) == []
    metrics = [{"domain_name": "finance"}]
    assert filter_metrics_by_permissions(metrics, roles="sales_rep") == []


def test_check_dashboard_access_success():
    """Test check_dashboard_access returns authorized domains for valid roles."""
    domains = check_dashboard_access("sales_rep")
    assert domains == {DOMAIN_SALES}


def test_check_dashboard_access_denied():
    """Test check_dashboard_access raises DashboardPermissionDeniedError when no domains authorized."""
    with pytest.raises(DashboardPermissionDeniedError):
        check_dashboard_access(roles="unknown_role")

    with pytest.raises(DashboardPermissionDeniedError):
        check_dashboard_access(roles=None)

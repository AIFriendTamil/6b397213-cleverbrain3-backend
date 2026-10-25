"""Unit tests for server-side dashboard permissions service."""

from tests.unit.test_dashboard_permissions import (  # noqa: F401
    test_check_dashboard_access_denied,
    test_check_dashboard_access_success,
    test_filter_metrics_by_permissions_dicts,
    test_filter_metrics_by_permissions_empty,
    test_filter_metrics_by_permissions_objects,
    test_get_authorized_domains_admin_roles,
    test_get_authorized_domains_custom_role_map,
    test_get_authorized_domains_multiple_roles,
    test_get_authorized_domains_specific_roles,
    test_get_authorized_domains_unknown_and_explicit,
    test_is_domain_authorized,
    test_normalize_roles,
)

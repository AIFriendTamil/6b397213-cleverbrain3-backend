"""Server-side role-based metric visibility and access control for dashboard domains."""

import logging
from typing import Any, Iterable, Optional, Union

logger = logging.getLogger(__name__)

# Canonical operational domains
DOMAIN_SALES = "sales"
DOMAIN_LEADS = "leads"
DOMAIN_SUPPORT = "support"
DOMAIN_FINANCE = "finance"

ALL_DOMAINS: set[str] = {
    DOMAIN_SALES,
    DOMAIN_LEADS,
    DOMAIN_SUPPORT,
    DOMAIN_FINANCE,
}

# Default role to authorized domain mapping
DEFAULT_ROLE_DOMAIN_MAP: dict[str, set[str]] = {
    "admin": ALL_DOMAINS,
    "executive": ALL_DOMAINS,
    "ceo": ALL_DOMAINS,
    "superadmin": ALL_DOMAINS,
    "dashboard_admin": ALL_DOMAINS,
    "director": ALL_DOMAINS,
    "sales_executive": {DOMAIN_SALES, DOMAIN_LEADS},
    "sales_manager": {DOMAIN_SALES, DOMAIN_LEADS},
    "sales_rep": {DOMAIN_SALES},
    "leads_manager": {DOMAIN_LEADS},
    "marketing_manager": {DOMAIN_LEADS},
    "lead_analyst": {DOMAIN_LEADS},
    "support_lead": {DOMAIN_SUPPORT},
    "support_manager": {DOMAIN_SUPPORT},
    "support_agent": {DOMAIN_SUPPORT},
    "finance_manager": {DOMAIN_FINANCE},
    "financial_analyst": {DOMAIN_FINANCE},
    "accountant": {DOMAIN_FINANCE},
    "all": ALL_DOMAINS,
    "*": ALL_DOMAINS,
}


class DashboardPermissionError(Exception):
    """Base exception for dashboard permission errors."""

    pass


class DashboardPermissionDeniedError(DashboardPermissionError):
    """Raised when a user lacks permission to access requested dashboard domain or metrics."""

    pass


def normalize_roles(roles: Optional[Union[str, Iterable[str]]]) -> set[str]:
    """Normalize user roles input into a set of clean lowercase role strings.

    Args:
        roles: User role string, comma-separated string, or iterable of role strings.

    Returns:
        Set of normalized lowercase role strings.
    """
    if not roles:
        return set()
    if isinstance(roles, str):
        parts = [r.strip().lower() for r in roles.split(",") if r.strip()]
        return set(parts)

    result = set()
    for role in roles:
        if isinstance(role, str) and role.strip():
            result.add(role.strip().lower())
    return result


def get_authorized_domains(
    roles: Optional[Union[str, Iterable[str]]] = None,
    explicit_domains: Optional[Iterable[str]] = None,
    role_map: Optional[dict[str, set[str]]] = None,
) -> set[str]:
    """Determine the set of authorized dashboard operational domains for a user.

    Args:
        roles: User role string or iterable of role strings.
        explicit_domains: Optional explicit domain names to override/grant directly.
        role_map: Optional custom role-to-domains dictionary. Uses DEFAULT_ROLE_DOMAIN_MAP if None.

    Returns:
        Set of authorized domain name strings (e.g., {'sales', 'leads'}).
    """
    authorized: set[str] = set()

    # 1. Include explicit domains if provided
    if explicit_domains:
        for domain_item in explicit_domains:
            if isinstance(domain_item, str) and domain_item.strip().lower() in ALL_DOMAINS:
                authorized.add(domain_item.strip().lower())

    # 2. Check roles against role mapping
    mapping = role_map if role_map is not None else DEFAULT_ROLE_DOMAIN_MAP
    clean_roles = normalize_roles(roles)

    for role in clean_roles:
        if role in mapping:
            authorized.update(mapping[role])
        elif role in ALL_DOMAINS:
            authorized.add(role)
        elif role.startswith("domain:") and role.split(":", 1)[1] in ALL_DOMAINS:
            authorized.add(role.split(":", 1)[1])

    return authorized


def is_domain_authorized(
    domain_name: str,
    roles: Optional[Union[str, Iterable[str]]] = None,
    explicit_domains: Optional[Iterable[str]] = None,
) -> bool:
    """Check whether a specific operational domain is authorized for a user.

    Args:
        domain_name: Operational domain name (e.g., 'sales').
        roles: User role(s).
        explicit_domains: Explicit domain overrides.

    Returns:
        True if the domain is authorized, False otherwise.
    """
    if not domain_name or not isinstance(domain_name, str):
        return False

    clean_domain = domain_name.strip().lower()
    authorized = get_authorized_domains(roles=roles, explicit_domains=explicit_domains)
    return clean_domain in authorized


def filter_metrics_by_permissions(
    metrics: list[Union[dict[str, Any], Any]],
    roles: Optional[Union[str, Iterable[str]]] = None,
    explicit_domains: Optional[Iterable[str]] = None,
) -> list[Union[dict[str, Any], Any]]:
    """Prune unauthorized domain metric items from a metrics list.

    Supports items as dictionaries with a 'domain_name' key or objects with a 'domain_name' attribute.

    Args:
        metrics: List of metric objects or dicts containing 'domain_name'.
        roles: User role string or collection of role strings.
        explicit_domains: Explicit domain overrides.

    Returns:
        Filtered list containing only authorized domain metric items.
    """
    if not metrics:
        return []

    authorized_domains = get_authorized_domains(roles=roles, explicit_domains=explicit_domains)
    filtered: list[Union[dict[str, Any], Any]] = []

    for item in metrics:
        domain = None
        if isinstance(item, dict):
            domain = item.get("domain_name")
        elif hasattr(item, "domain_name"):
            domain = getattr(item, "domain_name")

        if domain and isinstance(domain, str):
            clean_domain = domain.strip().lower()
            if clean_domain in authorized_domains:
                filtered.append(item)
            else:
                logger.debug("Pruned unauthorized domain metric: %s", clean_domain)

    return filtered


def check_dashboard_access(
    roles: Optional[Union[str, Iterable[str]]] = None,
    explicit_domains: Optional[Iterable[str]] = None,
) -> set[str]:
    """Enforce that a user has access to at least one dashboard domain.

    Args:
        roles: User role(s).
        explicit_domains: Explicit domain overrides.

    Returns:
        Set of authorized domains if user has access.

    Raises:
        DashboardPermissionDeniedError: If caller has no authorized domains.
    """
    authorized = get_authorized_domains(roles=roles, explicit_domains=explicit_domains)
    if not authorized:
        logger.warning("Access denied: user has no authorized dashboard domains (roles: %s)", roles)
        raise DashboardPermissionDeniedError(
            "Access denied. No authorized dashboard domains available for user role."
        )
    return authorized

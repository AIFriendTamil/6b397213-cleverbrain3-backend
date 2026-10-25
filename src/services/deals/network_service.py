"""Stakeholder graph network construction, role mappings, and cycle detection service."""

from dataclasses import asdict, dataclass, field
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.models.deal import Stakeholder

logger = logging.getLogger(__name__)


@dataclass
class StakeholderNode:
    """Node representation of a stakeholder in the network graph."""

    id: str
    name: str
    org_role: str
    contact_email: str
    deal_id: str
    parent_stakeholder_id: Optional[str] = None
    subordinate_ids: list[str] = field(default_factory=list)

    @property
    def email(self) -> str:
        """Alias for contact_email to ensure compatibility."""
        return self.contact_email

    def to_dict(self) -> dict[str, Any]:
        """Convert node instance to dictionary format."""
        return {
            "id": self.id,
            "name": self.name,
            "org_role": self.org_role,
            "contact_email": self.contact_email,
            "email": self.contact_email,
            "deal_id": self.deal_id,
            "parent_stakeholder_id": self.parent_stakeholder_id,
            "subordinate_ids": list(self.subordinate_ids),
        }


@dataclass
class StakeholderEdge:
    """Edge representation connecting two stakeholders in the network graph."""

    source_id: str
    target_id: str
    relationship_type: str = "reports_to"
    is_cycle_edge: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert edge instance to dictionary format."""
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relationship_type": self.relationship_type,
            "is_cycle_edge": self.is_cycle_edge,
        }


@dataclass
class StakeholderGraph:
    """Graph representation of stakeholders for a deal, including cycle detection analysis."""

    deal_id: str
    nodes: list[StakeholderNode]
    edges: list[StakeholderEdge]
    dag_edges: list[StakeholderEdge]
    has_cycles: bool
    detected_cycles: list[list[str]]
    root_stakeholder_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert graph instance to dictionary format."""
        return {
            "deal_id": self.deal_id,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "dag_edges": [edge.to_dict() for edge in self.dag_edges],
            "has_cycles": self.has_cycles,
            "detected_cycles": self.detected_cycles,
            "root_stakeholder_ids": list(self.root_stakeholder_ids),
        }


def _extract_stakeholder_data(st: Any) -> tuple[str, str, str, str, str, Optional[str]]:
    """Extract standard attributes from a Stakeholder ORM object, dict, or StakeholderNode.

    Args:
        st: Stakeholder entity, dict, or StakeholderNode instance.

    Returns:
        Tuple containing (id, name, org_role, contact_email, deal_id, parent_stakeholder_id).
    """
    if isinstance(st, dict):
        return (
            str(st.get("id", "")),
            str(st.get("name", "")),
            str(st.get("org_role", "")),
            str(st.get("contact_email") or st.get("email", "")),
            str(st.get("deal_id", "")),
            st.get("parent_stakeholder_id"),
        )
    if isinstance(st, StakeholderNode):
        return (
            st.id,
            st.name,
            st.org_role,
            st.contact_email,
            st.deal_id,
            st.parent_stakeholder_id,
        )
    return (
        getattr(st, "id", ""),
        getattr(st, "name", ""),
        getattr(st, "org_role", ""),
        getattr(st, "contact_email", getattr(st, "email", "")),
        getattr(st, "deal_id", ""),
        getattr(st, "parent_stakeholder_id", None),
    )


def detect_cycles(
    nodes: list[StakeholderNode],
    edges: list[StakeholderEdge],
) -> tuple[bool, list[list[str]], set[tuple[str, str]]]:
    """Detect cycles in a directed stakeholder graph using depth-first search.

    Args:
        nodes: List of StakeholderNode objects.
        edges: List of StakeholderEdge objects.

    Returns:
        Tuple containing:
            - has_cycles (bool): True if at least one cycle was detected.
            - detected_cycles (list[list[str]]): List of node ID sequences forming cycles.
            - cycle_edges (set[tuple[str, str]]): Set of (source_id, target_id) tuples causing cycles.
    """
    node_ids = {node.id for node in nodes}
    adj: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if edge.source_id in adj and edge.target_id in node_ids:
            adj[edge.source_id].append(edge.target_id)

    color: dict[str, int] = {node_id: 0 for node_id in node_ids}  # 0=WHITE, 1=GRAY, 2=BLACK
    path_stack: list[str] = []

    has_cycles = False
    detected_cycles: list[list[str]] = []
    cycle_edges: set[tuple[str, str]] = set()

    def dfs(u: str) -> None:
        nonlocal has_cycles
        color[u] = 1  # GRAY
        path_stack.append(u)

        for v in adj[u]:
            if color[v] == 1:  # GRAY -> Cycle detected!
                has_cycles = True
                cycle_edges.add((u, v))
                if v in path_stack:
                    idx = path_stack.index(v)
                    cycle_path = path_stack[idx:] + [v]
                    if cycle_path not in detected_cycles:
                        detected_cycles.append(cycle_path)
            elif color[v] == 0:  # WHITE
                dfs(v)

        path_stack.pop()
        color[u] = 2  # BLACK

    in_degree = {node_id: 0 for node_id in node_ids}
    for edge in edges:
        if edge.target_id in in_degree and edge.source_id in node_ids:
            in_degree[edge.target_id] += 1

    roots = [node_id for node_id, deg in in_degree.items() if deg == 0]
    non_roots = [node_id for node_id, deg in in_degree.items() if deg > 0]

    for start_node in roots + non_roots:
        if color[start_node] == 0:
            dfs(start_node)

    return has_cycles, detected_cycles, cycle_edges


def get_stakeholders_for_deal(db: Session, deal_id: str) -> list[Stakeholder]:
    """Retrieve all stakeholder records for a given deal from the database.

    Args:
        db: Active synchronous SQLAlchemy database Session.
        deal_id: Identifier of the target deal.

    Returns:
        List of Stakeholder ORM entities matching the deal_id.
    """
    return (
        db.query(Stakeholder)
        .filter(Stakeholder.deal_id == deal_id)
        .order_by(Stakeholder.created_at.asc())
        .all()
    )


def build_stakeholder_graph(
    stakeholders: list[Any],
    deal_id: str,
) -> StakeholderGraph:
    """Construct a stakeholder graph network and perform cycle detection to ensure a valid DAG.

    Args:
        stakeholders: List of Stakeholder ORM entities, dicts, or StakeholderNode objects.
        deal_id: Identifier of the target deal.

    Returns:
        StakeholderGraph instance containing nodes, raw edges, DAG edges, and cycle analysis.
    """
    if not stakeholders:
        return StakeholderGraph(
            deal_id=deal_id,
            nodes=[],
            edges=[],
            dag_edges=[],
            has_cycles=False,
            detected_cycles=[],
            root_stakeholder_ids=[],
        )

    nodes_map: dict[str, StakeholderNode] = {}
    for st in stakeholders:
        st_id, name, org_role, email, st_deal_id, parent_id = _extract_stakeholder_data(st)
        effective_deal_id = st_deal_id or deal_id
        node = StakeholderNode(
            id=st_id,
            name=name,
            org_role=org_role,
            contact_email=email,
            deal_id=effective_deal_id,
            parent_stakeholder_id=parent_id,
            subordinate_ids=[],
        )
        nodes_map[node.id] = node

    edges: list[StakeholderEdge] = []
    for node in nodes_map.values():
        if node.parent_stakeholder_id and node.parent_stakeholder_id in nodes_map:
            parent_node = nodes_map[node.parent_stakeholder_id]
            if node.id not in parent_node.subordinate_ids:
                parent_node.subordinate_ids.append(node.id)

            edges.append(
                StakeholderEdge(
                    source_id=node.parent_stakeholder_id,
                    target_id=node.id,
                    relationship_type="reports_to",
                    is_cycle_edge=False,
                )
            )

    nodes_list = list(nodes_map.values())
    has_cycles, detected_cycles, cycle_edges = detect_cycles(nodes_list, edges)

    processed_edges: list[StakeholderEdge] = []
    dag_edges: list[StakeholderEdge] = []

    for edge in edges:
        is_cycle = (edge.source_id, edge.target_id) in cycle_edges
        edge_obj = StakeholderEdge(
            source_id=edge.source_id,
            target_id=edge.target_id,
            relationship_type=edge.relationship_type,
            is_cycle_edge=is_cycle,
        )
        processed_edges.append(edge_obj)
        if not is_cycle:
            dag_edges.append(edge_obj)

    dag_target_ids = {e.target_id for e in dag_edges}
    root_ids = [n_id for n_id in nodes_map if n_id not in dag_target_ids]

    if has_cycles:
        logger.warning(
            "Cycle detected in stakeholder graph for deal %s. Cycles: %s. Formatted DAG with %d edges.",
            deal_id,
            detected_cycles,
            len(dag_edges),
        )

    return StakeholderGraph(
        deal_id=deal_id,
        nodes=nodes_list,
        edges=processed_edges,
        dag_edges=dag_edges,
        has_cycles=has_cycles,
        detected_cycles=detected_cycles,
        root_stakeholder_ids=root_ids,
    )


def get_deal_stakeholder_graph(db: Session, deal_id: str) -> StakeholderGraph:
    """Retrieve stakeholders for a deal and construct their graph network with cycle detection.

    Args:
        db: Active synchronous SQLAlchemy database Session.
        deal_id: Identifier of the target deal.

    Returns:
        StakeholderGraph instance for the deal.
    """
    stakeholders = get_stakeholders_for_deal(db, deal_id)
    return build_stakeholder_graph(stakeholders, deal_id)


def format_stakeholder_role_mapping(nodes: list[StakeholderNode]) -> dict[str, list[dict[str, Any]]]:
    """Group stakeholders by organizational role for structured role mapping displays.

    Args:
        nodes: List of StakeholderNode objects.

    Returns:
        Dictionary mapping organizational role titles to lists of stakeholder dictionaries.
    """
    role_map: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        role = node.org_role or "Unassigned Role"
        if role not in role_map:
            role_map[role] = []
        role_map[role].append(node.to_dict())
    return role_map


def format_interaction_history(node: StakeholderNode) -> dict[str, Any]:
    """Format interaction metadata context for a stakeholder.

    Args:
        node: StakeholderNode object.

    Returns:
        Dictionary containing stakeholder identity and interaction history details.
    """
    return {
        "stakeholder_id": node.id,
        "name": node.name,
        "org_role": node.org_role,
        "contact_email": node.contact_email,
        "has_parent": node.parent_stakeholder_id is not None,
        "subordinates_count": len(node.subordinate_ids),
        "interactions": [
            {
                "event_type": "stakeholder_added",
                "details": f"Stakeholder {node.name} ({node.org_role}) mapped to deal {node.deal_id}.",
            }
        ],
    }

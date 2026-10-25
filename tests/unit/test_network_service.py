"""Unit tests for stakeholder graph network construction and cycle detection in network_service.py."""

from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from src.models.base import Base, create_db_engine
from src.models.deal import Deal, DealStage, Stakeholder
from src.services.deals.network_service import (
    StakeholderEdge,
    StakeholderGraph,
    StakeholderNode,
    build_stakeholder_graph,
    detect_cycles,
    format_interaction_history,
    format_stakeholder_role_mapping,
    get_deal_stakeholder_graph,
    get_stakeholders_for_deal,
)


@pytest.fixture
def test_session():
    """Fixture providing an in-memory SQLite database session with created tables."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_stakeholder_node_and_edge_serialization():
    """Verify StakeholderNode and StakeholderEdge property access and dictionary serialization."""
    node = StakeholderNode(
        id="stk_1",
        name="Jane Doe",
        org_role="Chief Executive Officer",
        contact_email="jane@example.com",
        deal_id="deal_100",
        parent_stakeholder_id=None,
        subordinate_ids=["stk_2"],
    )

    assert node.email == "jane@example.com"
    node_dict = node.to_dict()
    assert node_dict["id"] == "stk_1"
    assert node_dict["email"] == "jane@example.com"
    assert node_dict["contact_email"] == "jane@example.com"
    assert node_dict["subordinate_ids"] == ["stk_2"]

    edge = StakeholderEdge(
        source_id="stk_1",
        target_id="stk_2",
        relationship_type="reports_to",
        is_cycle_edge=False,
    )
    edge_dict = edge.to_dict()
    assert edge_dict["source_id"] == "stk_1"
    assert edge_dict["target_id"] == "stk_2"
    assert edge_dict["is_cycle_edge"] is False


def test_build_stakeholder_graph_empty():
    """Verify build_stakeholder_graph handles empty stakeholder lists gracefully."""
    graph = build_stakeholder_graph([], deal_id="deal_empty")
    assert graph.deal_id == "deal_empty"
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.dag_edges == []
    assert graph.has_cycles is False
    assert graph.detected_cycles == []
    assert graph.root_stakeholder_ids == []

    graph_dict = graph.to_dict()
    assert graph_dict["deal_id"] == "deal_empty"
    assert graph_dict["has_cycles"] is False


def test_build_stakeholder_graph_tree_hierarchy():
    """Verify graph construction for a valid acyclic hierarchy (tree structure)."""
    # A (CEO) -> B (VP) -> C (Director)
    st_a = {
        "id": "stk_a",
        "name": "Alice CEO",
        "org_role": "CEO",
        "contact_email": "alice@example.com",
        "deal_id": "deal_tree",
        "parent_stakeholder_id": None,
    }
    st_b = {
        "id": "stk_b",
        "name": "Bob VP",
        "org_role": "VP",
        "contact_email": "bob@example.com",
        "deal_id": "deal_tree",
        "parent_stakeholder_id": "stk_a",
    }
    st_c = {
        "id": "stk_c",
        "name": "Charlie Director",
        "org_role": "Director",
        "contact_email": "charlie@example.com",
        "deal_id": "deal_tree",
        "parent_stakeholder_id": "stk_b",
    }

    graph = build_stakeholder_graph([st_a, st_b, st_c], deal_id="deal_tree")

    assert graph.has_cycles is False
    assert len(graph.nodes) == 3
    assert len(graph.edges) == 2
    assert len(graph.dag_edges) == 2
    assert graph.root_stakeholder_ids == ["stk_a"]

    # Verify subordinate_ids mapping
    node_a = next(n for n in graph.nodes if n.id == "stk_a")
    assert "stk_b" in node_a.subordinate_ids

    node_b = next(n for n in graph.nodes if n.id == "stk_b")
    assert "stk_c" in node_b.subordinate_ids


def test_detect_cycles_direct_two_node_cycle():
    """Verify detection and resolution of a direct 2-node circular reference (A -> B -> A)."""
    st_a = {
        "id": "stk_a",
        "name": "Alice",
        "org_role": "Exec",
        "contact_email": "alice@example.com",
        "deal_id": "deal_cycle",
        "parent_stakeholder_id": "stk_b",
    }
    st_b = {
        "id": "stk_b",
        "name": "Bob",
        "org_role": "Lead",
        "contact_email": "bob@example.com",
        "deal_id": "deal_cycle",
        "parent_stakeholder_id": "stk_a",
    }

    graph = build_stakeholder_graph([st_a, st_b], deal_id="deal_cycle")

    assert graph.has_cycles is True
    assert len(graph.detected_cycles) > 0
    # Confirm that dag_edges has excluded the cycle-creating edge to preserve a valid DAG
    assert len(graph.dag_edges) < len(graph.edges)
    assert any(e.is_cycle_edge for e in graph.edges)


def test_detect_cycles_three_node_cycle():
    """Verify cycle detection in a 3-node cycle loop (A -> B -> C -> A)."""
    st_a = {
        "id": "stk_1",
        "name": "Node 1",
        "org_role": "Role 1",
        "contact_email": "n1@example.com",
        "deal_id": "deal_loop3",
        "parent_stakeholder_id": "stk_3",
    }
    st_b = {
        "id": "stk_2",
        "name": "Node 2",
        "org_role": "Role 2",
        "contact_email": "n2@example.com",
        "deal_id": "deal_loop3",
        "parent_stakeholder_id": "stk_1",
    }
    st_c = {
        "id": "stk_3",
        "name": "Node 3",
        "org_role": "Role 3",
        "contact_email": "n3@example.com",
        "deal_id": "deal_loop3",
        "parent_stakeholder_id": "stk_2",
    }

    graph = build_stakeholder_graph([st_a, st_b, st_c], deal_id="deal_loop3")

    assert graph.has_cycles is True
    assert len(graph.detected_cycles) == 1
    cycle_path = graph.detected_cycles[0]
    assert cycle_path[0] == cycle_path[-1]  # Start and end at same node to form cycle

    # Verify DAG edges contains only acyclic edges
    dag_edges = graph.dag_edges
    assert len(dag_edges) == 2
    for edge in dag_edges:
        assert edge.is_cycle_edge is False


def test_detect_cycles_self_loop():
    """Verify detection of a self-referencing stakeholder (parent_stakeholder_id == self.id)."""
    st_self = {
        "id": "stk_self",
        "name": "Self Parent",
        "org_role": "Solo Lead",
        "contact_email": "self@example.com",
        "deal_id": "deal_self",
        "parent_stakeholder_id": "stk_self",
    }

    graph = build_stakeholder_graph([st_self], deal_id="deal_self")

    assert graph.has_cycles is True
    assert graph.detected_cycles == [["stk_self", "stk_self"]]
    assert len(graph.edges) == 1
    assert graph.edges[0].is_cycle_edge is True
    assert len(graph.dag_edges) == 0


def test_get_deal_stakeholder_graph_integration_with_db(test_session: Session):
    """Verify get_deal_stakeholder_graph retrieves ORM entities and constructs graph."""
    stage = DealStage(name="Discovery Stage", display_order=1)
    test_session.add(stage)
    test_session.commit()

    deal = Deal(
        title="Acme Corp Deal",
        monetary_value=Decimal("50000.00"),
        stage_id=stage.id,
    )
    test_session.add(deal)
    test_session.commit()

    st_exec = Stakeholder(
        deal_id=deal.id,
        name="Sarah Jenkins",
        org_role="Chief Technology Officer",
        contact_email="sarah@acme.com",
    )
    test_session.add(st_exec)
    test_session.commit()

    st_dev = Stakeholder(
        deal_id=deal.id,
        name="Dave Miller",
        org_role="DevOps Manager",
        contact_email="dave@acme.com",
        parent_stakeholder_id=st_exec.id,
    )
    test_session.add(st_dev)
    test_session.commit()

    db_stakeholders = get_stakeholders_for_deal(test_session, deal.id)
    assert len(db_stakeholders) == 2

    graph = get_deal_stakeholder_graph(test_session, deal.id)
    assert graph.deal_id == deal.id
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    assert graph.has_cycles is False
    assert graph.root_stakeholder_ids == [st_exec.id]

    node_exec = next(n for n in graph.nodes if n.id == st_exec.id)
    assert node_exec.name == "Sarah Jenkins"
    assert node_exec.org_role == "Chief Technology Officer"
    assert st_dev.id in node_exec.subordinate_ids


def test_format_stakeholder_role_mapping_and_interaction_history():
    """Verify role mapping grouping and interaction history formatting helpers."""
    node1 = StakeholderNode(
        id="s1",
        name="User One",
        org_role="VP Sales",
        contact_email="u1@example.com",
        deal_id="d1",
    )
    node2 = StakeholderNode(
        id="s2",
        name="User Two",
        org_role="VP Sales",
        contact_email="u2@example.com",
        deal_id="d1",
    )
    node3 = StakeholderNode(
        id="s3",
        name="User Three",
        org_role="Legal Counsel",
        contact_email="u3@example.com",
        deal_id="d1",
    )

    role_map = format_stakeholder_role_mapping([node1, node2, node3])
    assert "VP Sales" in role_map
    assert len(role_map["VP Sales"]) == 2
    assert "Legal Counsel" in role_map
    assert len(role_map["Legal Counsel"]) == 1

    interaction_info = format_interaction_history(node1)
    assert interaction_info["stakeholder_id"] == "s1"
    assert interaction_info["name"] == "User One"
    assert len(interaction_info["interactions"]) > 0

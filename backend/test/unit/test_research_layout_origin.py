"""Tests for the stack-below-existing layout origin (sub-project D anti-overlap)."""
from topix.integrations.research_layout_math import ORIGIN_Y, compute_layout_origin_y


def test_empty_board_uses_default_origin():
    """No existing nodes → start at the default origin (first pass)."""
    assert compute_layout_origin_y([], ["a"]) == ORIGIN_Y


def test_existing_nodes_push_origin_below_their_bottom():
    """Second pass must start below the lowest existing node + gap."""
    existing = [
        {"id": "old1", "deleted": False, "y": 80.0, "h": 200.0},
        {"id": "old2", "deleted": False, "y": 360.0, "h": 220.0},
    ]
    y = compute_layout_origin_y(existing, ["new1"])
    # max bottom = 360 + 220 + 120 = 700
    assert y == 700.0


def test_created_nodes_excluded_from_offset():
    """Nodes being laid out in this pass don't push the origin (avoid feedback)."""
    existing = [
        {"id": "new1", "deleted": False, "y": 9999.0, "h": 9999.0},
        {"id": "old1", "deleted": False, "y": 80.0, "h": 200.0},
    ]
    y = compute_layout_origin_y(existing, ["new1"])
    assert y == 400.0  # 80 + 200 + 120


def test_deleted_and_positionless_nodes_ignored():
    """Deleted nodes and nodes without a y don't affect the offset."""
    existing = [
        {"id": "d1", "deleted": True, "y": 9999.0, "h": 9999.0},
        {"id": "npo", "deleted": False, "y": None, "h": 200.0},
    ]
    assert compute_layout_origin_y(existing, ["a"]) == ORIGIN_Y
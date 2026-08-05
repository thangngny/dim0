from topix.integrations.dim0_mcp.server import TOOLS


def test_mcp_struct_tool_names_present():
    names = {t["name"] for t in TOOLS}
    assert {"dim0_set_node_kind", "dim0_reparent_node",
            "dim0_delete_subtree", "dim0_merge_nodes", "dim0_split_node"} <= names

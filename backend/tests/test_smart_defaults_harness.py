from backend.core.smart_defaults import SmartDefaults


def test_open_blender_enables_real_launch_tool() -> None:
    tools = SmartDefaults.suggest_tools(
        "Open Blender and load the scene",
        enabled=["calculator", "launch_app"],
    )

    assert "launch_app" in tools


def test_workspace_search_enables_code_discovery_primitives() -> None:
    tools = SmartDefaults.suggest_tools(
        "Find which file defines the provider form across the workspace",
        enabled=[
            "find_workspace_files",
            "search_workspace_text",
            "read_workspace_files",
        ],
    )

    assert tools == [
        "find_workspace_files",
        "search_workspace_text",
        "read_workspace_files",
    ]

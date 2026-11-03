from ssh_winrm_mcp.server import mcp


def test_server_registers_annotated_tools():
    tools = mcp._tool_manager._tools
    for name in (
        "remote_policy",
        "remote_inventory_init",
        "remote_profile_create",
        "remote_profile_update",
        "remote_execute",
        "remote_session_open",
        "remote_session_cleanup_idle",
        "remote_job_start",
        "remote_command_group_run",
    ):
        assert name in tools, name
    for name, tool in tools.items():
        assert tool.annotations is not None and tool.annotations.readOnlyHint is not None, name
    assert tools["remote_execute"].annotations.destructiveHint is True
    assert tools["remote_profile_list"].annotations.readOnlyHint is True
    assert len(tools) == 30

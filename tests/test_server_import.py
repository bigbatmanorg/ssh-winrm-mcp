import importlib
import sys
import types


def test_server_imports_with_fastmcp_contract(monkeypatch):
    class FakeFastMCP:
        def __init__(self, *args, **kwargs):
            self.tools = []
            self.resources = []

        def tool(self):
            def decorate(func):
                self.tools.append(func.__name__)
                return func

            return decorate

        def resource(self, uri):
            def decorate(func):
                self.resources.append(uri)
                return func

            return decorate

        def run(self, transport):
            return None

    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP

    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)
    sys.modules.pop("ssh_winrm_mcp.server", None)

    module = importlib.import_module("ssh_winrm_mcp.server")
    assert "remote_inventory_init" in module.mcp.tools
    assert "remote_profile_create" in module.mcp.tools
    assert "remote_profile_update" in module.mcp.tools
    assert "remote_execute" in module.mcp.tools
    assert "remote_session_open" in module.mcp.tools
    assert "remote_session_cleanup_idle" in module.mcp.tools
    assert "remote_job_start" in module.mcp.tools
    assert "remote_command_group_run" in module.mcp.tools

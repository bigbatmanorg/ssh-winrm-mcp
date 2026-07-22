from ssh_winrm_mcp.commands.registry import CommandRegistry


def test_builtin_groups_are_discovered():
    registry = CommandRegistry()
    groups = registry.list()
    assert "system" in groups
    assert "users" in groups
    assert "reboot" in groups["system"]
    assert "create" in groups["users"]


def test_system_service_plan():
    registry = CommandRegistry()
    plan = registry.build("system", "service", "ssh", {"name": "sshd", "operation": "restart"})
    assert "systemctl restart sshd" in plan.command
    assert plan.dangerous is True


def test_user_password_is_redacted():
    registry = CommandRegistry()
    plan = registry.build("users", "create", "winrm", {"username": "test", "password": "secret"})
    assert "secret" in plan.command
    assert "secret" not in (plan.redacted_command or "")


def test_external_commands_directory_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("SSH_WINRM_MCP_COMMANDS_DIR", str(tmp_path / "commands"))
    registry = CommandRegistry()
    status = registry.status(create_directory=True)
    assert status["directory_exists"] is True
    assert status["local_commands_directory"] == str(tmp_path / "commands")

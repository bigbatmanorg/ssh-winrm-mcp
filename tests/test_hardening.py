import asyncio

import pytest

from ssh_winrm_mcp import paths
from ssh_winrm_mcp.commands import system, users
from ssh_winrm_mcp.models import ConfigurationError, ConnectionConfig, merge_connection, migrate_stored
from ssh_winrm_mcp.operations import RemoteOperations
from ssh_winrm_mcp.policy import Policy
from ssh_winrm_mcp.profiles import ProfileStore
from ssh_winrm_mcp.sessions import MAX_FINISHED_JOBS, JobManager


@pytest.fixture(autouse=True)
def isolated_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    for name in ("SSH_WINRM_MCP_CONFIG", "SSH_WINRM_MCP_LOCAL_ROOT", "SSH_WINRM_MCP_COMMANDS_DIR"):
        monkeypatch.delenv(name, raising=False)


def ssh(**extra):
    return ConnectionConfig.from_dict({"protocol": "ssh", "host": "lab.example", "username": "u", **extra})


def test_local_paths_default_to_private_root(tmp_path):
    root = tmp_path / "data" / "ssh-winrm-mcp" / "files"
    assert paths.checked_local_path("a/b.txt") == root / "a" / "b.txt"
    for escape in ("../x", "/etc/passwd", "a/../../x"):
        with pytest.raises(PermissionError, match="outside"):
            paths.checked_local_path(escape)


def test_config_dirs_stay_protected_even_with_wide_root(monkeypatch, tmp_path):
    monkeypatch.setenv("SSH_WINRM_MCP_LOCAL_ROOT", "/")
    assert paths.checked_local_path("/etc/hostname") == paths.Path("/etc/hostname").resolve()
    plugin = tmp_path / "config" / "ssh-winrm-mcp" / "commands" / "evil.py"
    inventory = tmp_path / "config" / "ssh-winrm-mcp" / "inventory.json"
    for target in (plugin, inventory):
        with pytest.raises(PermissionError, match="configuration directory"):
            paths.checked_local_path(str(target))


def test_unknown_fields_rejected_ints_coerced_and_extra_explicit():
    with pytest.raises(ConfigurationError, match="unknown connection fields: pasword"):
        ssh(pasword="x")
    config = ssh(port="2222", connect_timeout="5", tags=["b", "a", "a"], extra={"team": "ops"})
    assert (config.port, config.connect_timeout, config.tags) == (2222, 5, ["a", "b"])
    assert config.extra == {"team": "ops"}
    with pytest.raises(ConfigurationError, match="port must be an integer"):
        ssh(port="ssh")


def test_legacy_profiles_with_top_level_keys_still_load(tmp_path):
    legacy = {"protocol": "ssh", "host": "h", "username": "u", "rack": "r1"}
    assert migrate_stored(legacy)["extra"] == {"rack": "r1"}
    assert merge_connection(legacy, {"port": 2200}).extra == {"rack": "r1"}
    store = ProfileStore(tmp_path / "inv.json")
    store._write({"old": legacy})
    assert store.get("old").extra == {"rack": "r1"}
    assert store.validate()["ok"]


def test_winrm_auth_validation():
    with pytest.raises(ConfigurationError, match="auth must be one of"):
        ConnectionConfig.from_dict({"protocol": "winrm", "host": "w", "username": "u", "auth": "digest"})
    with pytest.raises(ConfigurationError, match="certificate_pem"):
        ConnectionConfig.from_dict({"protocol": "winrm", "host": "w", "auth": "certificate"})


def test_policy_hosts_secrets_basic_and_adhoc(monkeypatch):
    monkeypatch.setenv("SSH_WINRM_MCP_ALLOWED_HOSTS", "*.example")
    policy = Policy.from_environment()
    policy.check(ssh(password_env="LAB_PASSWORD"))
    with pytest.raises(ConfigurationError, match="not allowed"):
        policy.check(ssh(host="attacker.net"))
    with pytest.raises(ConfigurationError, match="SECRET_ENV_PATTERNS"):
        policy.check(ssh(password_env="AWS_SECRET_ACCESS_KEY"))
    with pytest.raises(ConfigurationError, match="SECRET_ENV_PATTERNS"):
        policy.check(ssh(private_key_path="~/.ssh/k", private_key_passphrase_env="GITHUB_TOKEN"))
    basic_http = ConnectionConfig.from_dict(
        {"protocol": "winrm", "host": "w.example", "username": "u", "auth": "basic", "ssl": False}
    )
    with pytest.raises(ConfigurationError, match="unencrypted"):
        policy.check(basic_http)
    monkeypatch.setenv("SSH_WINRM_MCP_ALLOW_ADHOC", "false")
    with pytest.raises(ConfigurationError, match="ad-hoc connections are disabled"):
        Policy.from_environment().check_overrides({"host": "x.example"}, has_profile=True)
    Policy.from_environment().check_overrides({"read_timeout": 90}, has_profile=True)


def test_resolve_enforces_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("SSH_WINRM_MCP_ALLOWED_HOSTS", "*.example")
    ops = RemoteOperations(ProfileStore(tmp_path / "inv.json"))
    with pytest.raises(ConfigurationError, match="not allowed"):
        ops.resolve(None, {"protocol": "ssh", "host": "evil.net", "username": "u"})


def test_builtin_groups_never_prompt_for_sudo():
    for plan in (
        system._service("ssh", {"name": "nginx", "operation": "restart"}),
        system._reboot("ssh", {}),
        users._create("ssh", {"username": "bob", "password": "pw", "groups": ["wheel"]}),
        users._delete("ssh", {"username": "bob"}),
    ):
        assert "sudo " not in plan.command.replace("sudo -n ", ""), plan.command


async def test_finished_jobs_are_pruned():
    class Sessions:
        def get(self, name):
            return name

        async def execute(self, name, command, timeout):
            return None

    jobs = JobManager(Sessions())
    for i in range(MAX_FINISHED_JOBS + 20):
        jobs.start("s", f"echo {i}", timeout=5)
        await asyncio.sleep(0)
    await asyncio.sleep(0.01)
    jobs.start("s", "final", timeout=5)
    assert len(jobs._jobs) <= MAX_FINISHED_JOBS + 1

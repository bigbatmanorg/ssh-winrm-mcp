import os

import pytest

from ssh_winrm_mcp.models import ConfigurationError, ConnectionConfig, merge_connection


def test_ssh_profile_and_env_password(monkeypatch):
    monkeypatch.setenv("TEST_SSH_PASSWORD", "secret")
    config = ConnectionConfig.from_dict(
        {
            "protocol": "ssh",
            "host": "server.example",
            "username": "admin",
            "password_env": "TEST_SSH_PASSWORD",
        }
    )
    assert config.effective_port == 22
    assert config.resolved_password() == "secret"
    assert config.to_dict(redact=True)["password_env"] == "TEST_SSH_PASSWORD"


def test_merge_profile_with_adhoc_override():
    config = merge_connection(
        {"protocol": "ssh", "host": "old", "username": "root"},
        {"host": "new", "port": 2222},
    )
    assert config.host == "new"
    assert config.effective_port == 2222


def test_winrm_timeout_is_corrected():
    config = ConnectionConfig.from_dict(
        {
            "protocol": "winrm",
            "host": "win",
            "username": "Administrator",
            "operation_timeout": 60,
            "read_timeout": 30,
        }
    )
    assert config.read_timeout == 70


def test_invalid_protocol():
    with pytest.raises(ConfigurationError):
        ConnectionConfig.from_dict({"protocol": "telnet", "host": "x", "username": "u"})


def test_missing_environment_secret(monkeypatch):
    monkeypatch.delenv("NOT_SET_PASSWORD", raising=False)
    config = ConnectionConfig.from_dict(
        {
            "protocol": "ssh",
            "host": "server",
            "username": "admin",
            "password_env": "NOT_SET_PASSWORD",
        }
    )
    with pytest.raises(ConfigurationError):
        config.resolved_password()

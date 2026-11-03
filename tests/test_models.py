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


def test_winrm_disable_cert_check_environment_default(monkeypatch):
    monkeypatch.setenv("WINRM_DISABLE_SSL_CERT_CHECK", "1")
    config = ConnectionConfig.from_dict(
        {
            "protocol": "winrm",
            "host": "win",
            "username": "Administrator",
            "ssl": True,
        }
    )
    assert config.ssl is True
    assert config.cert_validation is False


def test_explicit_cert_validation_overrides_environment(monkeypatch):
    monkeypatch.setenv("WINRM_DISABLE_SSL_CERT_CHECK", "true")
    config = ConnectionConfig.from_dict(
        {
            "protocol": "winrm",
            "host": "win",
            "username": "Administrator",
            "cert_validation": True,
        }
    )
    assert config.cert_validation is True


def test_winrm_boolean_strings_are_normalized():
    config = ConnectionConfig.from_dict(
        {
            "protocol": "winrm",
            "host": "win",
            "username": "Administrator",
            "ssl": "true",
            "cert_validation": "false",
        }
    )
    assert config.ssl is True
    assert config.cert_validation is False


def test_invalid_winrm_boolean():
    with pytest.raises(ConfigurationError, match="cert_validation must be a boolean"):
        ConnectionConfig.from_dict(
            {
                "protocol": "winrm",
                "host": "win",
                "username": "Administrator",
                "cert_validation": "disabled",
            }
        )


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

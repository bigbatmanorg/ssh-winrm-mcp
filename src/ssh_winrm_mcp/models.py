from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Protocol = Literal["ssh", "winrm"]


class ConfigurationError(ValueError):
    """Raised when a connection configuration is invalid."""


WINRM_AUTH_METHODS = {"basic", "certificate", "credssp", "kerberos", "negotiate", "ntlm"}
INT_FIELDS = ("port", "connect_timeout", "read_timeout", "operation_timeout")


@dataclass(slots=True)
class ConnectionConfig:
    protocol: Protocol
    host: str
    port: int | None = None
    username: str | None = None

    # Prefer *_env in stored profiles. Direct values are supported for ad-hoc use.
    password: str | None = None
    password_env: str | None = None

    # SSH settings
    private_key_path: str | None = None
    private_key_passphrase: str | None = None
    private_key_passphrase_env: str | None = None
    verify_host_key: bool = True
    known_hosts_path: str | None = None
    ssh_agent: bool = True

    # WinRM / PSRP settings
    ssl: bool = True
    auth: str = "ntlm"
    cert_validation: bool = True
    encryption: str = "auto"
    path: str = "wsman"
    certificate_pem: str | None = None
    certificate_key_pem: str | None = None
    certificate_key_password: str | None = None
    certificate_key_password_env: str | None = None
    configuration_name: str = "Microsoft.PowerShell"

    # Shared timeouts
    connect_timeout: int = 30
    read_timeout: int = 60
    operation_timeout: int = 30

    # Useful override for script staging.
    remote_temp: str | None = None

    # Labels for filtering saved profiles.
    tags: list[str] | None = None

    # Free-form settings for custom command modules; must be passed explicitly as "extra".
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ConnectionConfig:
        if not isinstance(raw, dict):
            raise ConfigurationError("connection settings must be an object")

        data = dict(raw)

        # Keep transport selection and certificate trust independent.  The
        # environment override is useful for MCP clients that cannot persist
        # per-call defaults, but an explicit connection value always wins.
        if "cert_validation" not in data and _env_flag("WINRM_DISABLE_SSL_CERT_CHECK", default=False):
            data["cert_validation"] = False

        extra = data.pop("extra", None) or {}
        if not isinstance(extra, dict):
            raise ConfigurationError("extra must be an object")
        unknown = sorted(set(data) - set(cls.__dataclass_fields__))
        if unknown:
            raise ConfigurationError(f"unknown connection fields: {', '.join(unknown)}")
        for key in INT_FIELDS:
            if data.get(key) is not None:
                try:
                    data[key] = int(data[key])
                except (TypeError, ValueError) as exc:
                    raise ConfigurationError(f"{key} must be an integer") from exc

        protocol = str(data.get("protocol", "")).lower().strip()
        if protocol not in {"ssh", "winrm"}:
            raise ConfigurationError("protocol must be 'ssh' or 'winrm'")
        data["protocol"] = protocol

        host = str(data.get("host", "")).strip()
        if not host:
            raise ConfigurationError("host is required")
        data["host"] = host

        for key in ("ssl", "cert_validation", "verify_host_key", "ssh_agent"):
            if key in data:
                data[key] = _coerce_bool(data[key], key)

        config = cls(**data, extra=extra)
        config.validate()
        return config

    def validate(self) -> None:
        if self.port is not None and not (1 <= int(self.port) <= 65535):
            raise ConfigurationError("port must be between 1 and 65535")
        if self.protocol == "ssh" and not self.username:
            raise ConfigurationError("username is required for SSH")
        if self.protocol == "winrm" and self.auth != "certificate" and not self.username:
            raise ConfigurationError("username is required for this WinRM authentication method")
        if self.operation_timeout >= self.read_timeout:
            # pypsrp requires operation_timeout lower than read_timeout.
            self.read_timeout = self.operation_timeout + 10
        if self.encryption not in {"auto", "always", "never"}:
            raise ConfigurationError("WinRM encryption must be auto, always, or never")
        if min(self.connect_timeout, self.read_timeout, self.operation_timeout) <= 0:
            raise ConfigurationError("timeouts must be positive")
        if self.protocol == "winrm":
            self.auth = str(self.auth).strip().lower()
            if self.auth not in WINRM_AUTH_METHODS:
                raise ConfigurationError(f"auth must be one of {', '.join(sorted(WINRM_AUTH_METHODS))}")
            if self.auth == "certificate" and not (self.certificate_pem and self.certificate_key_pem):
                raise ConfigurationError("certificate auth requires certificate_pem and certificate_key_pem")
        if self.tags is not None:
            if not isinstance(self.tags, list) or not all(
                isinstance(t, str) and t.strip() for t in self.tags
            ):
                raise ConfigurationError("tags must be a list of non-empty strings")
            self.tags = sorted({t.strip() for t in self.tags})

    def resolved_password(self) -> str | None:
        return self.password if self.password is not None else _env_secret(self.password_env)

    def resolved_key_passphrase(self) -> str | None:
        if self.private_key_passphrase is not None:
            return self.private_key_passphrase
        return _env_secret(self.private_key_passphrase_env)

    def resolved_certificate_key_password(self) -> str | None:
        if self.certificate_key_password is not None:
            return self.certificate_key_password
        return _env_secret(self.certificate_key_password_env)

    def to_dict(self, *, redact: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if redact:
            for key in ("password", "private_key_passphrase", "certificate_key_password"):
                if data.get(key):
                    data[key] = "***REDACTED***"
        return {key: value for key, value in data.items() if value is not None and value != {}}

    def secret_env_names(self) -> list[str]:
        """Environment variables this profile reads secrets from (when no inline value is set)."""
        pairs = (
            (self.password, self.password_env),
            (self.private_key_passphrase, self.private_key_passphrase_env),
            (self.certificate_key_password, self.certificate_key_password_env),
        )
        return [env for inline, env in pairs if env and inline is None]

    @property
    def effective_port(self) -> int:
        if self.port:
            return int(self.port)
        if self.protocol == "ssh":
            return 22
        return 5986 if self.ssl else 5985


def _env_secret(name: str | None) -> str | None:
    if not name:
        return None
    value = os.getenv(name)
    if value is None:
        raise ConfigurationError(f"environment variable {name!r} is not set")
    return value


def _env_flag(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return _coerce_bool(value, name)


def _coerce_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ConfigurationError(f"{name} must be a boolean")


def migrate_stored(profile: dict[str, Any]) -> dict[str, Any]:
    """Profiles saved by v0.2 kept unknown keys at top level; move them under "extra"."""
    known = set(ConnectionConfig.__dataclass_fields__)
    legacy = {key: value for key, value in profile.items() if key not in known}
    migrated = {key: value for key, value in profile.items() if key in known}
    if legacy:
        migrated["extra"] = {**legacy, **(profile.get("extra") or {})}
    return migrated


def merge_connection(profile: dict[str, Any] | None, overrides: dict[str, Any] | None) -> ConnectionConfig:
    merged: dict[str, Any] = {}
    if profile:
        merged.update(migrate_stored(profile))
    if overrides:
        merged.update({key: value for key, value in overrides.items() if value is not None})
    return ConnectionConfig.from_dict(merged)

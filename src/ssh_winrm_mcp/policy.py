"""Operator-controlled limits on where the server connects and which secrets it may read.

Tool arguments come from an AI agent, which may be steered by untrusted content (prompt
injection). Without limits, an agent could point a connection at a host it controls and use a
`*_env` field to make the server send any of its environment variables there as a password.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from .models import ConfigurationError, ConnectionConfig, _coerce_bool

DEFAULT_SECRET_ENV_PATTERNS = ("*PASSWORD*", "*PASSWD*", "*_PASS", "*PASSPHRASE*")
# Fields an agent may still override per call when ad-hoc connections are disabled.
TUNING_FIELDS = {"connect_timeout", "read_timeout", "operation_timeout"}


def _patterns(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(p.strip() for p in raw.split(",") if p.strip())


@dataclass(frozen=True, slots=True)
class Policy:
    allowed_hosts: tuple[str, ...] = ()
    allow_adhoc: bool = True
    secret_env_patterns: tuple[str, ...] = DEFAULT_SECRET_ENV_PATTERNS
    allow_insecure_basic: bool = False

    @classmethod
    def from_environment(cls) -> Policy:
        return cls(
            allowed_hosts=tuple(p.lower() for p in _patterns("SSH_WINRM_MCP_ALLOWED_HOSTS")),
            allow_adhoc=_coerce_bool(
                os.getenv("SSH_WINRM_MCP_ALLOW_ADHOC", "true"), "SSH_WINRM_MCP_ALLOW_ADHOC"
            ),
            secret_env_patterns=_patterns("SSH_WINRM_MCP_SECRET_ENV_PATTERNS", DEFAULT_SECRET_ENV_PATTERNS),
            allow_insecure_basic=_coerce_bool(
                os.getenv("SSH_WINRM_MCP_ALLOW_INSECURE_BASIC", "false"), "SSH_WINRM_MCP_ALLOW_INSECURE_BASIC"
            ),
        )

    def check_overrides(self, connection: dict[str, Any] | None, *, has_profile: bool) -> None:
        if self.allow_adhoc or not connection:
            return
        forbidden = sorted(set(connection) - TUNING_FIELDS)
        if forbidden or not has_profile:
            raise ConfigurationError(
                "ad-hoc connections are disabled (SSH_WINRM_MCP_ALLOW_ADHOC=false); use a saved profile "
                f"and override only {', '.join(sorted(TUNING_FIELDS))}"
                + (f" (got {', '.join(forbidden)})" if forbidden else "")
            )

    def check(self, config: ConnectionConfig) -> ConnectionConfig:
        host = config.host.lower()
        if self.allowed_hosts and not any(fnmatchcase(host, pattern) for pattern in self.allowed_hosts):
            raise ConfigurationError(f"host {config.host!r} is not allowed by SSH_WINRM_MCP_ALLOWED_HOSTS")
        for name in config.secret_env_names():
            if not any(fnmatchcase(name.upper(), p.upper()) for p in self.secret_env_patterns):
                raise ConfigurationError(
                    f"environment variable {name!r} does not match SSH_WINRM_MCP_SECRET_ENV_PATTERNS "
                    f"({', '.join(self.secret_env_patterns)})"
                )
        insecure_basic = config.protocol == "winrm" and config.auth == "basic" and not config.ssl
        if insecure_basic and not self.allow_insecure_basic:
            raise ConfigurationError(
                "basic auth over plain HTTP sends the password unencrypted; use ssl=true, ntlm/kerberos, "
                "or set SSH_WINRM_MCP_ALLOW_INSECURE_BASIC=true"
            )
        return config

    def summary(self) -> dict[str, Any]:
        return {
            "allowed_hosts": list(self.allowed_hosts) or "any",
            "allow_adhoc": self.allow_adhoc,
            "secret_env_patterns": list(self.secret_env_patterns),
            "allow_insecure_basic": self.allow_insecure_basic,
        }

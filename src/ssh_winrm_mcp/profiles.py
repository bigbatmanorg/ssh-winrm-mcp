from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any

from .models import ConnectionConfig, migrate_stored

INVENTORY_VERSION = 1
SECRET_FIELDS = ("password", "private_key_passphrase", "certificate_key_password")


def default_inventory_path() -> Path:
    """Return a persistent per-user inventory path suitable for uvx execution."""
    configured = os.getenv("SSH_WINRM_MCP_CONFIG")
    if configured:
        return Path(configured).expanduser().resolve()

    if os.name == "nt":
        base = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "ssh-winrm-mcp" / "inventory.json"


class ProfileStore:
    """Atomic JSON inventory for reusable SSH and WinRM connection profiles."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else default_inventory_path()
        self._lock = RLock()

    def status(self) -> dict[str, Any]:
        with self._lock:
            load_error: str | None = None
            try:
                profile_count: int | None = len(self._load())
            except Exception as exc:  # noqa: BLE001 - status must still work for a damaged file.
                profile_count = None
                load_error = f"{type(exc).__name__}: {exc}"

            parent = self.path.parent
            writable = os.access(self.path, os.W_OK) if self.path.exists() else os.access(parent, os.W_OK)
            if not parent.exists():
                writable = os.access(parent.parent if parent.parent.exists() else Path.home(), os.W_OK)
            return {
                "path": str(self.path),
                "exists": self.path.exists(),
                "profile_count": profile_count,
                "writable": writable,
                "version": INVENTORY_VERSION,
                "configured_by_env": bool(os.getenv("SSH_WINRM_MCP_CONFIG")),
                "load_error": load_error,
            }

    def initialize(self, *, overwrite: bool = False) -> dict[str, Any]:
        """Create an empty inventory. Existing data is preserved unless overwrite is true."""
        with self._lock:
            if self.path.exists() and not overwrite:
                return {"ok": True, "created": False, **self.status()}
            self._write({})
            return {"ok": True, "created": True, **self.status()}

    def _load_document(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": INVENTORY_VERSION, "profiles": {}}
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON inventory {self.path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"invalid inventory file: {self.path}")

        # Accept the old simple {name: settings} shape as well as the versioned shape.
        profiles = raw.get("profiles", raw)
        if not isinstance(profiles, dict):
            raise ValueError(f"invalid profiles object in inventory: {self.path}")
        return {"version": int(raw.get("version", INVENTORY_VERSION)), "profiles": profiles}

    def _load(self) -> dict[str, dict[str, Any]]:
        raw_profiles = self._load_document()["profiles"]
        profiles: dict[str, dict[str, Any]] = {}
        for name, value in raw_profiles.items():
            if not isinstance(name, str) or not isinstance(value, dict):
                raise ValueError(f"invalid profile entry in inventory: {name!r}")
            profiles[name] = dict(value)
        return profiles

    def _write(self, profiles: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": INVENTORY_VERSION, "profiles": profiles}
        fd, temp_name = tempfile.mkstemp(prefix="inventory-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def validate(self) -> dict[str, Any]:
        with self._lock:
            try:
                profiles = self._load()
            except Exception as exc:  # noqa: BLE001 - return a repairable validation result.
                return {
                    "ok": False,
                    "path": str(self.path),
                    "valid_profiles": [],
                    "invalid_profiles": {},
                    "profile_count": None,
                    "inventory_error": f"{type(exc).__name__}: {exc}",
                }

            valid: list[str] = []
            errors: dict[str, str] = {}
            for name, settings in sorted(profiles.items()):
                try:
                    ConnectionConfig.from_dict(migrate_stored(settings))
                except Exception as exc:  # noqa: BLE001 - report all malformed profile errors.
                    errors[name] = f"{type(exc).__name__}: {exc}"
                else:
                    valid.append(name)
            return {
                "ok": not errors,
                "path": str(self.path),
                "valid_profiles": valid,
                "invalid_profiles": errors,
                "profile_count": len(profiles),
                "inventory_error": None,
            }

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._load())

    def get_raw(self, name: str) -> dict[str, Any]:
        with self._lock:
            profiles = self._load()
            if name not in profiles:
                raise KeyError(f"profile {name!r} does not exist")
            return dict(profiles[name])

    def get(self, name: str) -> ConnectionConfig:
        return ConnectionConfig.from_dict(migrate_stored(self.get_raw(name)))

    def list_safe(self, *, protocol: str | None = None, tag: str | None = None) -> dict[str, dict[str, Any]]:
        selected: dict[str, dict[str, Any]] = {}
        with self._lock:
            for name, settings in sorted(self._load().items()):
                config = ConnectionConfig.from_dict(migrate_stored(settings))
                safe = config.to_dict(redact=True)
                if protocol and config.protocol != protocol.lower().strip():
                    continue
                if tag:
                    tags = safe.get("tags", [])
                    if not isinstance(tags, list) or tag not in [str(item) for item in tags]:
                        continue
                selected[name] = safe
        return selected

    def create(self, name: str, settings: dict[str, Any]) -> ConnectionConfig:
        return self.save(name, settings, overwrite=False)

    def save(self, name: str, settings: dict[str, Any], *, overwrite: bool = False) -> ConnectionConfig:
        clean_name = _clean_name(name)
        config = ConnectionConfig.from_dict(settings)
        with self._lock:
            profiles = self._load()
            if clean_name in profiles and not overwrite:
                raise ValueError(f"profile {clean_name!r} already exists")
            profiles[clean_name] = config.to_dict(redact=False)
            self._write(profiles)
        return config

    def update(
        self,
        name: str,
        changes: dict[str, Any],
        *,
        remove_fields: list[str] | None = None,
        replace: bool = False,
    ) -> ConnectionConfig:
        clean_name = _clean_name(name)
        if not isinstance(changes, dict):
            raise ValueError("changes must be an object")
        with self._lock:
            profiles = self._load()
            if clean_name not in profiles:
                raise KeyError(f"profile {clean_name!r} does not exist")
            updated = dict(changes) if replace else {**migrate_stored(profiles[clean_name]), **changes}
            for field in remove_fields or []:
                updated.pop(str(field), None)
            config = ConnectionConfig.from_dict(updated)
            profiles[clean_name] = config.to_dict(redact=False)
            self._write(profiles)
            return config

    def rename(self, name: str, new_name: str, *, overwrite: bool = False) -> bool:
        source = _clean_name(name)
        destination = _clean_name(new_name)
        with self._lock:
            profiles = self._load()
            if source not in profiles:
                raise KeyError(f"profile {source!r} does not exist")
            if destination in profiles and destination != source and not overwrite:
                raise ValueError(f"profile {destination!r} already exists")
            value = profiles.pop(source)
            profiles[destination] = value
            self._write(profiles)
            return True

    def delete(self, name: str) -> bool:
        clean_name = _clean_name(name)
        with self._lock:
            profiles = self._load()
            if clean_name not in profiles:
                return False
            del profiles[clean_name]
            self._write(profiles)
            return True


def has_inline_secret(settings: dict[str, Any]) -> bool:
    return any(settings.get(key) is not None for key in SECRET_FIELDS)


def _clean_name(name: str) -> str:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("profile name is required")
    if any(character in clean_name for character in "\\/\0"):
        raise ValueError("profile name cannot contain path separators or NUL")
    return clean_name

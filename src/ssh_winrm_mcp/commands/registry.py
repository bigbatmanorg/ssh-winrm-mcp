from __future__ import annotations

import importlib
import importlib.util
import os
import pkgutil
from pathlib import Path
from types import ModuleType
from typing import Any

from ..profiles import default_inventory_path
from .base import CommandAction, CommandPlan


def default_commands_dir() -> Path:
    configured = os.getenv("SSH_WINRM_MCP_COMMANDS_DIR")
    if configured:
        return Path(configured).expanduser()
    return default_inventory_path().parent / "commands"


class CommandRegistry:
    """Discovers built-in groups and persistent local plug-ins from the commands directory."""

    def __init__(self) -> None:
        self.groups: dict[str, dict[str, CommandAction]] = {}
        self.external_dir = default_commands_dir()
        self.load_errors: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        self.groups.clear()
        self.load_errors.clear()
        package = importlib.import_module("ssh_winrm_mcp.commands")
        for module_info in pkgutil.iter_modules(package.__path__):
            if module_info.name in {"base", "registry"} or module_info.name.startswith("_"):
                continue
            module = importlib.import_module(f"ssh_winrm_mcp.commands.{module_info.name}")
            self._register_module(module, fallback_name=module_info.name)

        if self.external_dir.is_dir():
            for path in sorted(self.external_dir.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                try:
                    spec = importlib.util.spec_from_file_location(f"ssh_winrm_mcp_external_{path.stem}", path)
                    if not spec or not spec.loader:
                        raise RuntimeError("could not create import specification")
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    self._register_module(module, fallback_name=path.stem)
                except Exception as exc:  # noqa: BLE001 - report plug-in errors without breaking the server.
                    self.load_errors[path.name] = f"{type(exc).__name__}: {exc}"

    def ensure_external_dir(self) -> Path:
        self.external_dir.mkdir(parents=True, exist_ok=True)
        return self.external_dir

    def _register_module(self, module: ModuleType, *, fallback_name: str) -> None:
        group_name = str(getattr(module, "GROUP_NAME", fallback_name)).strip()
        raw_actions = getattr(module, "ACTIONS", None)
        if not isinstance(raw_actions, dict):
            return
        actions: dict[str, CommandAction] = {}
        for name, value in raw_actions.items():
            if isinstance(value, CommandAction):
                actions[str(name)] = value
        if actions:
            self.groups[group_name] = actions

    def list(self) -> dict[str, list[str]]:
        return {group: sorted(actions) for group, actions in sorted(self.groups.items())}

    def status(self, *, create_directory: bool = False) -> dict[str, Any]:
        if create_directory:
            self.ensure_external_dir()
        return {
            "groups": self.list(),
            "local_commands_directory": str(self.external_dir),
            "directory_exists": self.external_dir.is_dir(),
            "load_errors": dict(self.load_errors),
        }

    def describe(self, group: str) -> dict[str, Any]:
        actions = self._get_group(group)
        return {
            "group": group,
            "actions": {
                name: {
                    "description": action.description,
                    "parameters": action.parameters,
                }
                for name, action in sorted(actions.items())
            },
        }

    def build(self, group: str, action: str, protocol: str, parameters: dict[str, Any] | None) -> CommandPlan:
        actions = self._get_group(group)
        try:
            command_action = actions[action]
        except KeyError as exc:
            raise KeyError(f"command action {group}.{action} does not exist") from exc
        return command_action.builder(protocol, parameters or {})

    def _get_group(self, group: str) -> dict[str, CommandAction]:
        try:
            return self.groups[group]
        except KeyError as exc:
            raise KeyError(f"command group {group!r} does not exist") from exc

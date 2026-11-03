from __future__ import annotations

import shlex
from typing import Any

from .base import CommandAction, CommandPlan

GROUP_NAME = "users"


def _required(params: dict[str, Any], name: str) -> str:
    value = str(params.get(name, "")).strip()
    if not value:
        raise ValueError(f"parameter {name!r} is required")
    return value


def _list(protocol: str, params: dict[str, Any]) -> CommandPlan:
    del params
    if protocol == "winrm":
        return CommandPlan(
            "Get-LocalUser | Select-Object Name,Enabled,LastLogon,PasswordRequired,UserMayChangePassword "
            "| ConvertTo-Json -Depth 3",
            "powershell",
            "List local Windows users",
        )
    return CommandPlan("getent passwd", "sh", "List Unix users")


def _create(protocol: str, params: dict[str, Any]) -> CommandPlan:
    username = _required(params, "username")
    password = params.get("password")
    groups = params.get("groups") or []
    if not isinstance(groups, list):
        raise ValueError("groups must be a list")

    if protocol == "winrm":
        user = _ps_quote(username)
        pieces: list[str] = []
        if password is not None:
            secret = _ps_quote(str(password))
            pieces.append(f"$ucPassword = ConvertTo-SecureString {secret} -AsPlainText -Force")
            pieces.append(f"New-LocalUser -Name {user} -Password $ucPassword")
        else:
            pieces.append(f"New-LocalUser -Name {user} -NoPassword")
        for group in groups:
            pieces.append(f"Add-LocalGroupMember -Group {_ps_quote(str(group))} -Member {user}")
        command = "; ".join(pieces)
        redacted = (
            command.replace(_ps_quote(str(password)), "'***REDACTED***'") if password is not None else command
        )
        return CommandPlan(command, "powershell", f"Create local Windows user {username}", True, redacted)

    q_user = shlex.quote(username)
    pieces = [f"sudo -n useradd -m {q_user}"]
    if password is not None:
        pair = shlex.quote(f"{username}:{password}")
        pieces.append(f"printf '%s\\n' {pair} | sudo -n chpasswd")
    if groups:
        pieces.append(f"sudo -n usermod -aG {shlex.quote(','.join(str(group) for group in groups))} {q_user}")
    command = " && ".join(pieces)
    redacted = (
        command.replace(shlex.quote(f"{username}:{password}"), "'***REDACTED***'")
        if password is not None
        else command
    )
    return CommandPlan(command, "sh", f"Create Unix user {username}", True, redacted)


def _delete(protocol: str, params: dict[str, Any]) -> CommandPlan:
    username = _required(params, "username")
    remove_home = bool(params.get("remove_home", False))
    if protocol == "winrm":
        command = f"Remove-LocalUser -Name {_ps_quote(username)}"
        return CommandPlan(command, "powershell", f"Delete local Windows user {username}", True)
    flag = " --remove" if remove_home else ""
    return CommandPlan(
        f"sudo -n userdel{flag} {shlex.quote(username)}",
        "sh",
        f"Delete Unix user {username}",
        True,
    )


def _group(protocol: str, params: dict[str, Any], *, add: bool) -> CommandPlan:
    username = _required(params, "username")
    group = _required(params, "group")
    if protocol == "winrm":
        verb = "Add" if add else "Remove"
        command = f"{verb}-LocalGroupMember -Group {_ps_quote(group)} -Member {_ps_quote(username)}"
        return CommandPlan(
            command, "powershell", f"{verb.lower()} {username} {'to' if add else 'from'} {group}", True
        )
    if add:
        command = f"sudo -n usermod -aG {shlex.quote(group)} {shlex.quote(username)}"
    else:
        command = f"sudo -n gpasswd -d {shlex.quote(username)} {shlex.quote(group)}"
    return CommandPlan(command, "sh", f"Change group membership for {username}", True)


def _add_group(protocol: str, params: dict[str, Any]) -> CommandPlan:
    return _group(protocol, params, add=True)


def _remove_group(protocol: str, params: dict[str, Any]) -> CommandPlan:
    return _group(protocol, params, add=False)


def _enabled(protocol: str, params: dict[str, Any], *, enabled: bool) -> CommandPlan:
    username = _required(params, "username")
    if protocol == "winrm":
        verb = "Enable" if enabled else "Disable"
        return CommandPlan(
            f"{verb}-LocalUser -Name {_ps_quote(username)}",
            "powershell",
            f"{verb.lower()} local Windows user {username}",
            True,
        )
    flag = "-U" if enabled else "-L"
    return CommandPlan(
        f"sudo -n usermod {flag} {shlex.quote(username)}", "sh", f"Change login state for {username}", True
    )


def _enable(protocol: str, params: dict[str, Any]) -> CommandPlan:
    return _enabled(protocol, params, enabled=True)


def _disable(protocol: str, params: dict[str, Any]) -> CommandPlan:
    return _enabled(protocol, params, enabled=False)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


ACTIONS = {
    "list": CommandAction("List local users", _list, {}),
    "create": CommandAction(
        "Create a local user and optionally assign groups",
        _create,
        {"username": "required", "password": "optional", "groups": "optional list"},
    ),
    "delete": CommandAction(
        "Delete a local user",
        _delete,
        {"username": "required", "remove_home": "Unix only; boolean"},
    ),
    "add_to_group": CommandAction(
        "Add a user to a local group",
        _add_group,
        {"username": "required", "group": "required"},
    ),
    "remove_from_group": CommandAction(
        "Remove a user from a local group",
        _remove_group,
        {"username": "required", "group": "required"},
    ),
    "enable": CommandAction("Enable or unlock a local user", _enable, {"username": "required"}),
    "disable": CommandAction("Disable or lock a local user", _disable, {"username": "required"}),
}

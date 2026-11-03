from __future__ import annotations

import shlex
from typing import Any

from .base import CommandAction, CommandPlan

GROUP_NAME = "system"


def _required(params: dict[str, Any], name: str) -> str:
    value = str(params.get(name, "")).strip()
    if not value:
        raise ValueError(f"parameter {name!r} is required")
    return value


def _info(protocol: str, params: dict[str, Any]) -> CommandPlan:
    del params
    if protocol == "winrm":
        command = (
            "Get-ComputerInfo | Select-Object CsName,WindowsProductName,WindowsVersion,OsArchitecture,"
            "CsTotalPhysicalMemory,OsLastBootUpTime | ConvertTo-Json -Depth 3"
        )
        return CommandPlan(command, "powershell", "Collect Windows host information")
    command = "uname -a; printf '\\n'; cat /etc/os-release 2>/dev/null || true; printf '\\n'; uptime"
    return CommandPlan(command, "sh", "Collect Unix host information")


def _disk(protocol: str, params: dict[str, Any]) -> CommandPlan:
    del params
    if protocol == "winrm":
        return CommandPlan(
            "Get-Volume | Select-Object DriveLetter,FileSystemLabel,FileSystem,Size,SizeRemaining,HealthStatus "
            "| ConvertTo-Json -Depth 3",
            "powershell",
            "Show Windows volume usage",
        )
    return CommandPlan("df -hT", "sh", "Show filesystem usage")


def _processes(protocol: str, params: dict[str, Any]) -> CommandPlan:
    limit = int(params.get("limit", 30))
    if protocol == "winrm":
        return CommandPlan(
            f"Get-Process | Sort-Object CPU -Descending | Select-Object -First {limit} "
            "Name,Id,CPU,WorkingSet,Path | ConvertTo-Json -Depth 3",
            "powershell",
            f"Show top {limit} Windows processes",
        )
    command = f"(ps aux --sort=-%cpu 2>/dev/null || ps aux) | head -n {limit + 1}"
    return CommandPlan(command, "sh", f"Show top {limit} processes")


def _service(protocol: str, params: dict[str, Any]) -> CommandPlan:
    name = _required(params, "name")
    operation = str(params.get("operation", "status")).lower()
    if operation not in {"status", "start", "stop", "restart", "enable", "disable"}:
        raise ValueError("operation must be status, start, stop, restart, enable, or disable")
    if protocol == "winrm":
        qname = _ps_quote(name)
        mapping = {
            "status": f"Get-Service -Name {qname} | Select-Object * | ConvertTo-Json -Depth 3",
            "start": f"Start-Service -Name {qname}; Get-Service -Name {qname}",
            "stop": f"Stop-Service -Name {qname} -Force; Get-Service -Name {qname}",
            "restart": f"Restart-Service -Name {qname} -Force; Get-Service -Name {qname}",
            "enable": f"Set-Service -Name {qname} -StartupType Automatic; Get-Service -Name {qname}",
            "disable": f"Set-Service -Name {qname} -StartupType Disabled; Get-Service -Name {qname}",
        }
        return CommandPlan(
            mapping[operation], "powershell", f"{operation} Windows service {name}", operation != "status"
        )
    command = f"sudo -n systemctl {operation} {shlex.quote(name)}"
    return CommandPlan(command, "sh", f"{operation} systemd service {name}", operation != "status")


def _reboot(protocol: str, params: dict[str, Any]) -> CommandPlan:
    delay = int(params.get("delay_seconds", 0))
    if protocol == "winrm":
        if delay > 0:
            command = f"shutdown.exe /r /t {delay} /f"
        else:
            command = "Restart-Computer -Force"
        return CommandPlan(command, "powershell", "Restart the Windows host", dangerous=True)
    if delay > 0:
        command = f"nohup sh -c 'sleep {delay}; sudo -n systemctl reboot' >/dev/null 2>&1 &"
    else:
        command = "sudo -n systemctl reboot"
    return CommandPlan(command, "sh", "Restart the Unix host", dangerous=True)


def _shutdown(protocol: str, params: dict[str, Any]) -> CommandPlan:
    delay = int(params.get("delay_seconds", 0))
    if protocol == "winrm":
        return CommandPlan(f"shutdown.exe /s /t {delay} /f", "powershell", "Shut down the Windows host", True)
    if delay > 0:
        command = f"nohup sh -c 'sleep {delay}; sudo -n systemctl poweroff' >/dev/null 2>&1 &"
    else:
        command = "sudo -n systemctl poweroff"
    return CommandPlan(command, "sh", "Shut down the Unix host", True)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


ACTIONS = {
    "info": CommandAction("Collect operating-system and hardware basics", _info, {}),
    "disk_usage": CommandAction("Show disk or volume usage", _disk, {}),
    "processes": CommandAction(
        "Show processes ordered by CPU use", _processes, {"limit": "integer, default 30"}
    ),
    "service": CommandAction(
        "Read or change a service state",
        _service,
        {"name": "service name", "operation": "status|start|stop|restart|enable|disable"},
    ),
    "reboot": CommandAction("Restart the remote host", _reboot, {"delay_seconds": "integer, default 0"}),
    "shutdown": CommandAction(
        "Power off the remote host", _shutdown, {"delay_seconds": "integer, default 0"}
    ),
}

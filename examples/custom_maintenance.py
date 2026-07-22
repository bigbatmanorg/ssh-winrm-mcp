"""Example external command group.

Copy this file to the local commands directory reported by
remote_command_groups(create_directory=true), then call remote_command_groups(reload=true).
"""

from typing import Any

from ssh_winrm_mcp.commands.base import CommandAction, CommandPlan

GROUP_NAME = "maintenance"


def package_updates(protocol: str, params: dict[str, Any]) -> CommandPlan:
    del params
    if protocol == "winrm":
        # Replace this with your own package-management standard.
        return CommandPlan(
            "Get-Package | Sort-Object Name | Select-Object Name,Version | ConvertTo-Json -Depth 3",
            "powershell",
            "List installed Windows packages",
        )
    return CommandPlan(
        "sudo apt-get update && apt list --upgradable",
        "sh",
        "Refresh APT metadata and list upgrades",
    )


ACTIONS = {
    "package_updates": CommandAction(
        description="List package/update information",
        builder=package_updates,
        parameters={},
    )
}

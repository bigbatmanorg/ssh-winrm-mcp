from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CommandPlan:
    command: str
    shell: str
    summary: str
    dangerous: bool = False
    redacted_command: str | None = None


@dataclass(slots=True)
class CommandAction:
    description: str
    builder: Callable[[str, dict[str, Any]], CommandPlan]
    parameters: dict[str, str]

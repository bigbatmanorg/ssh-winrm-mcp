from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class RemoteResult:
    protocol: str
    host: str
    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_seconds: float = 0.0
    ok: bool = True

    def to_dict(self, output_limit: int | None = None) -> dict[str, Any]:
        if output_limit is None:
            output_limit = int(os.getenv("SSH_WINRM_MCP_MAX_OUTPUT_CHARS", "200000"))
        data = asdict(self)
        data["stdout"], out_truncated = truncate_text(self.stdout, output_limit)
        data["stderr"], err_truncated = truncate_text(self.stderr, output_limit)
        data["output_truncated"] = out_truncated or err_truncated
        return data


def truncate_text(value: str | None, limit: int) -> tuple[str, bool]:
    text = value or ""
    if limit <= 0 or len(text) <= limit:
        return text, False
    half = max(1, limit // 2)
    removed = len(text) - (half * 2)
    marker = f"\n\n... [truncated {removed} characters] ...\n\n"
    return text[:half] + marker + text[-half:], True

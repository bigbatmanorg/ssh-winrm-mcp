from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .backends import make_backend
from .backends.base import PersistentRemoteSession
from .models import ConnectionConfig
from .results import RemoteResult


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass(slots=True)
class SessionRecord:
    name: str
    remote: PersistentRemoteSession
    config: ConnectionConfig
    created_at: datetime = field(default_factory=_now)
    last_used_at: datetime = field(default_factory=_now)

    def safe_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "protocol": self.config.protocol,
            "host": self.config.host,
            "port": self.config.effective_port,
            "username": self.config.username,
            "created_at": _iso(self.created_at),
            "last_used_at": _iso(self.last_used_at),
            "alive": self.remote.alive(),
        }


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._lock = asyncio.Lock()

    async def open(self, name: str, config: ConnectionConfig, *, shell: str | None = None) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("session name is required")
        async with self._lock:
            if clean_name in self._sessions:
                raise ValueError(f"session {clean_name!r} already exists")
            remote = await make_backend(config).open_session(shell=shell)
            record = SessionRecord(name=clean_name, remote=remote, config=config)
            self._sessions[clean_name] = record
            return record.safe_dict()

    def get(self, name: str) -> SessionRecord:
        try:
            return self._sessions[name]
        except KeyError as exc:
            raise KeyError(f"session {name!r} does not exist") from exc

    async def execute(self, name: str, command: str, *, timeout: float) -> RemoteResult:
        record = self.get(name)
        if not record.remote.alive():
            raise RuntimeError(f"session {name!r} is no longer alive")
        record.last_used_at = _now()
        result = await record.remote.execute(command, timeout=timeout)
        record.last_used_at = _now()
        return result

    async def interrupt(self, name: str) -> None:
        record = self.get(name)
        await record.remote.interrupt()
        record.last_used_at = _now()

    def list(self) -> list[dict[str, Any]]:
        return [self._sessions[name].safe_dict() for name in sorted(self._sessions)]

    async def close(self, name: str) -> bool:
        async with self._lock:
            record = self._sessions.pop(name, None)
        if record is None:
            return False
        await record.remote.close()
        return True

    async def close_all(self) -> int:
        async with self._lock:
            records = list(self._sessions.values())
            self._sessions.clear()
        await asyncio.gather(*(record.remote.close() for record in records), return_exceptions=True)
        return len(records)

    async def cleanup_idle(self, max_idle_seconds: float) -> list[str]:
        if max_idle_seconds <= 0:
            return []
        now = _now()
        expired = [
            record.name
            for record in self._sessions.values()
            if (now - record.last_used_at).total_seconds() >= max_idle_seconds
        ]
        for name in expired:
            await self.close(name)
        return expired


@dataclass(slots=True)
class JobRecord:
    id: str
    session_name: str
    command: str
    task: asyncio.Task[RemoteResult]
    created_at: datetime = field(default_factory=_now)
    finished_at: datetime | None = None
    result: RemoteResult | None = None
    error: str | None = None
    cancelled: bool = False

    def status(self) -> str:
        if self.cancelled:
            return "cancelled"
        if not self.task.done():
            return "running"
        if self.error:
            return "failed"
        return "completed"

    def summary(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "session_name": self.session_name,
            "command": self.command,
            "status": self.status(),
            "created_at": _iso(self.created_at),
            "finished_at": _iso(self.finished_at),
            "error": self.error,
        }


MAX_FINISHED_JOBS = 100


class JobManager:
    def __init__(self, sessions: SessionManager) -> None:
        self._sessions = sessions
        self._jobs: dict[str, JobRecord] = {}

    def start(self, session_name: str, command: str, *, timeout: float) -> dict[str, Any]:
        # Validate before scheduling so callers get immediate feedback.
        self._sessions.get(session_name)
        job_id = uuid.uuid4().hex[:12]
        task = asyncio.create_task(self._sessions.execute(session_name, command, timeout=timeout))
        record = JobRecord(id=job_id, session_name=session_name, command=command, task=task)
        self._jobs[job_id] = record
        task.add_done_callback(lambda completed, jid=job_id: self._capture(jid, completed))
        self._prune()
        return record.summary()

    def _prune(self) -> None:
        # Finished jobs keep their full output; cap how many are retained in memory.
        finished = sorted(
            (r for r in self._jobs.values() if r.task.done()), key=lambda r: r.finished_at or r.created_at
        )
        for record in finished[: max(0, len(finished) - MAX_FINISHED_JOBS)]:
            self._jobs.pop(record.id, None)

    def _capture(self, job_id: str, task: asyncio.Task[RemoteResult]) -> None:
        record = self._jobs.get(job_id)
        if record is None:
            return
        record.finished_at = _now()
        if task.cancelled():
            record.cancelled = True
            return
        try:
            record.result = task.result()
        except Exception as exc:  # noqa: BLE001 - errors are returned to MCP clients.
            record.error = f"{type(exc).__name__}: {exc}"

    def get(self, job_id: str) -> JobRecord:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise KeyError(f"job {job_id!r} does not exist") from exc

    def list(self, session_name: str | None = None) -> list[dict[str, Any]]:
        records = self._jobs.values()
        if session_name:
            records = [record for record in records if record.session_name == session_name]
        return [
            record.summary() for record in sorted(records, key=lambda item: item.created_at, reverse=True)
        ]

    async def result(self, job_id: str, *, wait: bool = False, timeout: float = 0) -> dict[str, Any]:
        record = self.get(job_id)
        if wait and not record.task.done():
            if timeout > 0:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(record.task), timeout=timeout)
            else:
                await asyncio.shield(record.task)
        payload = record.summary()
        if record.result is not None:
            payload["result"] = record.result.to_dict()
        return payload

    async def cancel(self, job_id: str) -> dict[str, Any]:
        record = self.get(job_id)
        if record.task.done():
            return record.summary()
        with contextlib.suppress(Exception):
            await self._sessions.interrupt(record.session_name)
        record.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await record.task
        record.cancelled = True
        record.finished_at = _now()
        return record.summary()

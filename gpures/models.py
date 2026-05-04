from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Gpu:
    gpu_id: str
    name: str
    uuid: str | None = None
    memory_mb: int | None = None


@dataclass(frozen=True)
class Reservation:
    id: int
    username: str
    gpu_ids: list[str]
    start_time: str
    end_time: str
    reason: str | None
    created_at: str
    canceled_at: str | None = None
    canceled_by: str | None = None

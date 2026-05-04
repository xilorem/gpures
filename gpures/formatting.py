from __future__ import annotations

from datetime import datetime
from typing import Iterable

from gpures.constants import ISO_FMT, TIME_FMT
from gpures.models import Reservation
from gpures.store import from_utc_text


def fmt_dt(value: str | datetime) -> str:
    if isinstance(value, str):
        value = from_utc_text(value)
    return value.astimezone(datetime.now().astimezone().tzinfo).strftime(TIME_FMT)


def fmt_span(start: datetime, end: datetime) -> str:
    return f"{fmt_dt(start)} -> {fmt_dt(end)}"


def fmt_short_time(value: datetime) -> str:
    return value.astimezone(datetime.now().astimezone().tzinfo).strftime("%m-%d %H:%M")


def status_label(
    start_text: str | None, end_text: str | None, canceled_at: str | None = None
) -> str:
    if canceled_at:
        return "canceled"
    if not start_text or not end_text:
        return "free"
    now = datetime.now().astimezone()
    start = from_utc_text(start_text)
    end = from_utc_text(end_text)
    if end < now:
        return "expired"
    if start <= now <= end:
        return "active"
    return "upcoming"


def table(headers: list[str], rows: Iterable[Iterable[object]]) -> str:
    materialized = [[str(cell) for cell in row] for row in rows]
    widths = [
        max(len(header), *(len(row[index]) for row in materialized))
        if materialized
        else len(header)
        for index, header in enumerate(headers)
    ]
    fmt = "  ".join(f"{{:<{width}}}" for width in widths)
    lines = [fmt.format(*headers), fmt.format(*["-" * width for width in widths])]
    lines.extend(fmt.format(*row) for row in materialized)
    return "\n".join(lines)


def format_reservations(rows: list[Reservation]) -> str:
    return table(
        ["ID", "GPUs", "Status", "User", "From", "To", "Reason"],
        [
            [
                row.id,
                ",".join(row.gpu_ids),
                status_label(row.start_time, row.end_time, row.canceled_at),
                row.username,
                fmt_dt(row.start_time),
                fmt_dt(row.end_time),
                row.reason or "",
            ]
            for row in rows
        ],
    )


def timeline_header(start: datetime, end: datetime, width: int) -> str:
    if width <= 0:
        return ""
    chars = [" "] * width
    labels = [
        (0, fmt_short_time(start)),
        (width // 2, fmt_short_time(start + ((end - start) / 2))),
        (max(0, width - len(fmt_short_time(end))), fmt_short_time(end)),
    ]
    for pos, label in labels:
        for offset, ch in enumerate(label):
            if 0 <= pos + offset < width:
                chars[pos + offset] = ch
    return "".join(chars)


def details_for_gpu(
    gpu_id: str,
    positions: list[tuple[int, int, object]],
    start: datetime,
    end: datetime,
) -> str:
    if not positions:
        return f"GPU {gpu_id}: free for the whole window"
    parts = []
    for _, _, row in positions[:3]:
        parts.append(
            f"id {row['id']} {row['username']} {fmt_span(
                max(from_utc_text(row['start_time']), start),
                min(from_utc_text(row['end_time']), end),
            )}"
        )
    extra = "" if len(positions) <= 3 else f"  +{len(positions) - 3} more"
    return f"GPU {gpu_id}: " + " | ".join(parts) + extra

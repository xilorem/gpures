from __future__ import annotations

import re
from datetime import datetime, timedelta


def local_tz():
    return datetime.now().astimezone().tzinfo


def parse_duration(value: str) -> timedelta:
    text = value.strip().lower().replace(" ", "")
    if not text:
        raise ValueError("duration is empty")

    pos = 0
    total = timedelta()
    for match in re.finditer(r"(\d+)([mhd])", text):
        if match.start() != pos:
            raise ValueError(f"invalid duration: {value}")
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "m":
            total += timedelta(minutes=amount)
        elif unit == "h":
            total += timedelta(hours=amount)
        elif unit == "d":
            total += timedelta(days=amount)
        pos = match.end()

    if pos != len(text) or total <= timedelta():
        raise ValueError(f"invalid duration: {value}")
    return total


def parse_local_datetime(value: str, *, now: datetime | None = None) -> datetime:
    text = value.strip()
    lowered = text.lower()
    now = now or datetime.now().astimezone().replace(microsecond=0)

    if lowered == "now":
        return now

    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=local_tz())

    raise ValueError(
        f"invalid time {value!r}; use 'now', 'YYYY-MM-DD HH:MM', or ISO-like local time"
    )


def parse_gpu_list(value: str) -> list[str]:
    gpu_ids = [item.strip() for item in value.split(",") if item.strip()]
    if not gpu_ids:
        raise ValueError("no GPU ids provided")
    return gpu_ids

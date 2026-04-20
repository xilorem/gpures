from __future__ import annotations

import argparse
import contextlib
import fcntl
import getpass
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

DEFAULT_DATA_DIR = Path(os.environ.get("GPURES_HOME", "/var/lib/gpures"))
DEFAULT_DB_NAME = "reservations.sqlite"
DEFAULT_MAX_ADVANCE = timedelta(days=7)
TIME_FMT = "%Y-%m-%d %H:%M"
ISO_FMT = "%Y-%m-%dT%H:%M:%S%z"


@dataclass(frozen=True)
class Gpu:
    gpu_id: str
    name: str
    uuid: str | None = None
    memory_mb: int | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def local_tz():
    return datetime.now().astimezone().tzinfo


def to_utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime(ISO_FMT)


def from_utc_text(value: str) -> datetime:
    return datetime.strptime(value, ISO_FMT).astimezone(local_tz())


def fmt_dt(value: str | datetime) -> str:
    if isinstance(value, str):
        value = from_utc_text(value)
    return value.astimezone(local_tz()).strftime(TIME_FMT)


def fmt_span(start: datetime, end: datetime) -> str:
    return f"{fmt_dt(start)} -> {fmt_dt(end)}"


def fmt_short_time(value: datetime) -> str:
    return value.astimezone(local_tz()).strftime("%m-%d %H:%M")


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


def detect_gpus() -> list[Gpu]:
    configured = os.environ.get("GPURES_GPUS")
    if configured:
        return [Gpu(gpu_id=gpu_id, name=f"GPU {gpu_id}") for gpu_id in parse_gpu_list(configured)]

    command = [
        "nvidia-smi",
        "--query-gpu=index,uuid,name,memory.total",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []

    gpus: list[Gpu] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",", 3)]
        if len(parts) != 4:
            continue
        gpu_id, uuid, name, memory = parts
        try:
            memory_mb = int(memory)
        except ValueError:
            memory_mb = None
        gpus.append(Gpu(gpu_id=gpu_id, uuid=uuid, name=name, memory_mb=memory_mb))
    return gpus


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


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.db_path = data_dir / DEFAULT_DB_NAME
        self.lock_path = data_dir / ".lock"

    def ensure_data_dir(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            raise RuntimeError(
                f"cannot create {self.data_dir}; install the package as root or set GPURES_HOME"
            ) from exc

    @contextlib.contextmanager
    def lock(self):
        self.ensure_data_dir()
        self.lock_path.touch(exist_ok=True)
        with self.lock_path.open("r+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @contextlib.contextmanager
    def conn(self):
        self.ensure_data_dir()
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def init_db(self) -> None:
        with self.lock(), self.conn() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS gpus (
                    gpu_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    uuid TEXT,
                    memory_mb INTEGER,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reservations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    canceled_at TEXT,
                    canceled_by TEXT
                );

                CREATE TABLE IF NOT EXISTS reservation_gpus (
                    reservation_id INTEGER NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
                    gpu_id TEXT NOT NULL REFERENCES gpus(gpu_id),
                    PRIMARY KEY (reservation_id, gpu_id)
                );

                CREATE INDEX IF NOT EXISTS idx_reservations_active_time
                    ON reservations(canceled_at, start_time, end_time);
                CREATE INDEX IF NOT EXISTS idx_reservation_gpus_gpu
                    ON reservation_gpus(gpu_id);
                """
            )

    def sync_gpus(self, gpus: list[Gpu] | None = None) -> int:
        gpus = detect_gpus() if gpus is None else gpus
        if not gpus:
            return 0

        now = to_utc_text(utcnow())
        with self.lock(), self.conn() as con:
            for gpu in gpus:
                con.execute(
                    """
                    INSERT INTO gpus(gpu_id, name, uuid, memory_mb, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(gpu_id) DO UPDATE SET
                        name = excluded.name,
                        uuid = excluded.uuid,
                        memory_mb = excluded.memory_mb,
                        updated_at = excluded.updated_at
                    """,
                    (gpu.gpu_id, gpu.name, gpu.uuid, gpu.memory_mb, now),
                )
        return len(gpus)

    def configured_gpus(self) -> list[sqlite3.Row]:
        with self.conn() as con:
            return con.execute(
                "SELECT gpu_id, name, uuid, memory_mb FROM gpus ORDER BY CAST(gpu_id AS INTEGER), gpu_id"
            ).fetchall()

    def find_free_gpus(self, start: datetime, end: datetime) -> list[str]:
        s, e = to_utc_text(start), to_utc_text(end)
        with self.conn() as con:
            busy = {
                row["gpu_id"]
                for row in con.execute(
                    """
                    SELECT DISTINCT rg.gpu_id
                    FROM reservation_gpus rg
                    JOIN reservations r ON r.id = rg.reservation_id
                    WHERE r.canceled_at IS NULL
                      AND r.start_time < ?
                      AND r.end_time > ?
                    """,
                    (e, s),
                )
            }
            return [
                row["gpu_id"]
                for row in con.execute(
                    "SELECT gpu_id FROM gpus ORDER BY CAST(gpu_id AS INTEGER), gpu_id"
                )
                if row["gpu_id"] not in busy
            ]

    def reserve(
        self,
        username: str,
        gpu_ids: list[str],
        start: datetime,
        end: datetime,
        reason: str | None,
    ) -> int:
        s, e = to_utc_text(start), to_utc_text(end)
        with self.lock(), self.conn() as con:
            known = {row["gpu_id"] for row in con.execute("SELECT gpu_id FROM gpus")}
            missing = [gpu_id for gpu_id in gpu_ids if gpu_id not in known]
            if missing:
                raise RuntimeError(f"unknown GPU ids: {', '.join(missing)}")

            conflicts = con.execute(
                """
                SELECT rg.gpu_id, r.id, r.username, r.start_time, r.end_time
                FROM reservation_gpus rg
                JOIN reservations r ON r.id = rg.reservation_id
                WHERE rg.gpu_id IN ({})
                  AND r.canceled_at IS NULL
                  AND r.start_time < ?
                  AND r.end_time > ?
                ORDER BY CAST(rg.gpu_id AS INTEGER), rg.gpu_id, r.start_time
                """.format(",".join("?" for _ in gpu_ids)),
                (*gpu_ids, e, s),
            ).fetchall()
            if conflicts:
                details = "; ".join(
                    f"GPU {row['gpu_id']} reserved by {row['username']} "
                    f"({fmt_dt(row['start_time'])} to {fmt_dt(row['end_time'])}, id {row['id']})"
                    for row in conflicts
                )
                raise RuntimeError(f"reservation conflicts: {details}")

            cursor = con.execute(
                """
                INSERT INTO reservations(username, start_time, end_time, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (username, s, e, reason, to_utc_text(utcnow())),
            )
            reservation_id = int(cursor.lastrowid)
            con.executemany(
                "INSERT INTO reservation_gpus(reservation_id, gpu_id) VALUES (?, ?)",
                [(reservation_id, gpu_id) for gpu_id in gpu_ids],
            )
            return reservation_id

    def status_rows(self) -> list[sqlite3.Row]:
        now = to_utc_text(utcnow())
        with self.conn() as con:
            return con.execute(
                """
                SELECT
                    g.gpu_id,
                    g.name,
                    r.id AS reservation_id,
                    r.username,
                    r.start_time,
                    r.end_time,
                    r.reason
                FROM gpus g
                LEFT JOIN reservations r ON r.id = (
                    SELECT r2.id
                    FROM reservation_gpus rg2
                    JOIN reservations r2 ON r2.id = rg2.reservation_id
                    WHERE rg2.gpu_id = g.gpu_id
                      AND r2.canceled_at IS NULL
                      AND r2.end_time > ?
                    ORDER BY
                      CASE WHEN r2.start_time <= ? AND r2.end_time > ? THEN 0 ELSE 1 END,
                      r2.start_time
                    LIMIT 1
                )
                ORDER BY CAST(g.gpu_id AS INTEGER), g.gpu_id
                """,
                (now, now, now),
            ).fetchall()

    def reservation_rows(self, *, username: str | None = None) -> list[sqlite3.Row]:
        where = "WHERE r.username = ?" if username else ""
        params = (username,) if username else ()
        with self.conn() as con:
            return con.execute(
                f"""
                SELECT
                    r.id,
                    r.username,
                    r.start_time,
                    r.end_time,
                    r.reason,
                    r.canceled_at,
                    GROUP_CONCAT(rg.gpu_id, ',') AS gpu_ids
                FROM reservations r
                JOIN reservation_gpus rg ON rg.reservation_id = r.id
                {where}
                GROUP BY r.id
                ORDER BY r.start_time, r.id
                """,
                params,
            ).fetchall()

    def reservations_between(self, start: datetime, end: datetime) -> list[sqlite3.Row]:
        s, e = to_utc_text(start), to_utc_text(end)
        with self.conn() as con:
            return con.execute(
                """
                SELECT
                    rg.gpu_id,
                    r.id,
                    r.username,
                    r.start_time,
                    r.end_time,
                    r.reason
                FROM reservation_gpus rg
                JOIN reservations r ON r.id = rg.reservation_id
                WHERE r.canceled_at IS NULL
                  AND r.start_time < ?
                  AND r.end_time > ?
                ORDER BY CAST(rg.gpu_id AS INTEGER), rg.gpu_id, r.start_time, r.id
                """,
                (e, s),
            ).fetchall()

    def cancel(self, reservation_id: int, username: str, *, is_admin: bool = False) -> None:
        with self.lock(), self.conn() as con:
            row = con.execute(
                "SELECT username, canceled_at FROM reservations WHERE id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"reservation {reservation_id} not found")
            if row["canceled_at"] is not None:
                raise RuntimeError(f"reservation {reservation_id} is already canceled")
            if row["username"] != username and not is_admin:
                raise RuntimeError("only the reservation owner or root can cancel it")

            con.execute(
                "UPDATE reservations SET canceled_at = ?, canceled_by = ? WHERE id = ?",
                (to_utc_text(utcnow()), username, reservation_id),
            )


def normalize_window(args) -> tuple[datetime, datetime]:
    now = datetime.now().astimezone().replace(microsecond=0)
    start = parse_local_datetime(args.start, now=now)
    if args.duration and args.until:
        raise ValueError("use either --for or --until, not both")
    if not args.duration and not args.until:
        raise ValueError("use one of --for or --until")

    if args.duration:
        end = start + parse_duration(args.duration)
    else:
        end = parse_local_datetime(args.until, now=now)

    if start < now - timedelta(seconds=60):
        raise ValueError("reservations cannot start in the past")
    if end <= start:
        raise ValueError("reservation end must be after start")
    horizon_end = now + DEFAULT_MAX_ADVANCE
    if start > horizon_end:
        raise ValueError("reservations must start within the next 7 days")
    if end > horizon_end:
        raise ValueError("reservations must end within the next 7 days")
    return start, end


def get_store(args) -> Store:
    return Store(args.home)


def refresh_gpus(store: Store) -> None:
    store.init_db()
    store.sync_gpus()


def normalize_calendar_window(start_text: str, duration_text: str) -> tuple[datetime, datetime]:
    now = datetime.now().astimezone().replace(microsecond=0)
    start = parse_local_datetime(start_text, now=now)
    horizon_end = now + DEFAULT_MAX_ADVANCE
    if start < now - timedelta(seconds=60):
        raise ValueError("calendar cannot start in the past")
    if start > horizon_end:
        raise ValueError("calendar start must be within the next 7 days")

    duration = parse_duration(duration_text)
    end = min(start + duration, horizon_end)
    if end <= start:
        raise ValueError("calendar window is empty")
    return start, end


def status_label(start_text: str | None, end_text: str | None, canceled_at: str | None = None) -> str:
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


def cmd_init(args) -> None:
    store = get_store(args)
    store.init_db()
    synced = 0 if args.no_sync else store.sync_gpus()
    print(f"Initialized {store.db_path}")
    if synced:
        print(f"Synced {synced} GPU(s)")
    elif not args.no_sync:
        print("No GPUs detected; set GPURES_GPUS or run on a host with nvidia-smi")


def cmd_gpus(args) -> None:
    store = get_store(args)
    refresh_gpus(store)
    rows = store.configured_gpus()
    if not rows:
        print("No GPUs configured or detected")
        return
    print(
        table(
            ["GPU", "Name", "Memory MiB", "UUID"],
            [
                [
                    row["gpu_id"],
                    row["name"],
                    row["memory_mb"] if row["memory_mb"] is not None else "-",
                    row["uuid"] or "-",
                ]
                for row in rows
            ],
        )
    )


def cmd_status(args) -> None:
    store = get_store(args)
    refresh_gpus(store)
    rows = store.status_rows()
    if not rows:
        print("No GPUs configured or detected")
        return
    print(
        table(
            ["GPU", "Name", "Status", "User", "From", "To", "Reason"],
            [
                [
                    row["gpu_id"],
                    row["name"],
                    status_label(row["start_time"], row["end_time"]),
                    row["username"] or "-",
                    fmt_dt(row["start_time"]) if row["start_time"] else "-",
                    fmt_dt(row["end_time"]) if row["end_time"] else "-",
                    row["reason"] or "",
                ]
                for row in rows
            ],
        )
    )


def cmd_list(args) -> None:
    store = get_store(args)
    store.init_db()
    rows = store.reservation_rows()
    if not rows:
        print("No reservations")
        return
    print(format_reservations(rows))


def cmd_mine(args) -> None:
    store = get_store(args)
    store.init_db()
    rows = store.reservation_rows(username=getpass.getuser())
    if not rows:
        print("No reservations for current user")
        return
    print(format_reservations(rows))


def format_reservations(rows: list[sqlite3.Row]) -> str:
    return table(
        ["ID", "GPUs", "Status", "User", "From", "To", "Reason"],
        [
            [
                row["id"],
                row["gpu_ids"],
                status_label(row["start_time"], row["end_time"], row["canceled_at"]),
                row["username"],
                fmt_dt(row["start_time"]),
                fmt_dt(row["end_time"]),
                row["reason"] or "",
            ]
            for row in rows
        ],
    )


def clip_tui_window(start: datetime, duration: timedelta) -> tuple[datetime, datetime]:
    now = datetime.now().astimezone().replace(microsecond=0)
    horizon_end = now + DEFAULT_MAX_ADVANCE
    if start < now:
        start = now
    if start > horizon_end:
        start = max(now, horizon_end - duration)
    end = min(start + duration, horizon_end)
    if end <= start:
        start = max(now, horizon_end - timedelta(hours=1))
        end = horizon_end
    return start, end


def reservation_positions(
    reservations: list[sqlite3.Row],
    start: datetime,
    end: datetime,
    width: int,
) -> dict[str, list[tuple[int, int, sqlite3.Row]]]:
    total_seconds = max(1, int((end - start).total_seconds()))
    by_gpu: dict[str, list[tuple[int, int, sqlite3.Row]]] = {}
    for row in reservations:
        reserved_start = max(from_utc_text(row["start_time"]), start)
        reserved_end = min(from_utc_text(row["end_time"]), end)
        if reserved_end <= reserved_start:
            continue
        left = int(((reserved_start - start).total_seconds() / total_seconds) * width)
        right = int(((reserved_end - start).total_seconds() / total_seconds) * width)
        right = max(left + 1, right)
        by_gpu.setdefault(row["gpu_id"], []).append((left, min(width, right), row))
    return by_gpu


def cmd_calendar(args) -> None:
    try:
        import curses
    except ImportError as exc:
        raise RuntimeError("calendar TUI requires Python curses support") from exc

    start, end = normalize_calendar_window(args.start, args.duration)
    duration = end - start
    curses.wrapper(run_calendar_tui, args, start, duration)


def run_calendar_tui(stdscr, args, start: datetime, duration: timedelta) -> None:
    import curses

    store = get_store(args)
    scroll = 0
    selected = 0

    curses.curs_set(0)
    stdscr.nodelay(False)
    if curses.has_colors():
        curses.start_color()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_GREEN)
        curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_YELLOW)

    while True:
        start, end = clip_tui_window(start, duration)
        refresh_gpus(store)
        gpus = store.configured_gpus()
        reservations = store.reservations_between(start, end)
        selected = min(selected, max(0, len(gpus) - 1))
        scroll = min(scroll, max(0, len(gpus) - 1))

        stdscr.erase()
        height, width = stdscr.getmaxyx()
        timeline_x = 14
        timeline_width = max(10, width - timeline_x - 1)
        positions = reservation_positions(reservations, start, end, timeline_width)

        safe_add(stdscr, 0, 0, f"gpures calendar TUI  {fmt_span(start, end)}", width, curses.A_BOLD)
        safe_add(stdscr, 1, 0, "q quit  arrows/hjkl move  H/L shift 6h  [/ ] shift 1d  +/- zoom  r refresh", width)
        safe_add(stdscr, 3, 0, "GPU", 12, curses.A_BOLD)
        safe_add(stdscr, 3, timeline_x, timeline_header(start, end, timeline_width), timeline_width, curses.A_BOLD)

        visible_rows = max(0, height - 7)
        if selected < scroll:
            scroll = selected
        if selected >= scroll + visible_rows:
            scroll = selected - visible_rows + 1

        for index, gpu in enumerate(gpus[scroll : scroll + visible_rows], start=scroll):
            y = 4 + index - scroll
            attr = curses.A_REVERSE if index == selected else curses.A_NORMAL
            safe_add(stdscr, y, 0, f"{gpu['gpu_id']} {gpu['name']}", 12, attr)
            safe_add(stdscr, y, timeline_x, "." * timeline_width, timeline_width)
            for left, right, reservation in positions.get(gpu["gpu_id"], []):
                block_attr = curses.color_pair(2) if curses.has_colors() else curses.A_REVERSE
                label = f"#{reservation['id']} {reservation['username']}"
                block_width = max(1, right - left)
                safe_add(stdscr, y, timeline_x + left, label[:block_width].ljust(block_width, "#"), block_width, block_attr)

        detail_y = height - 2
        if gpus:
            selected_gpu = gpus[selected]["gpu_id"]
            details = details_for_gpu(selected_gpu, positions.get(selected_gpu, []), start, end)
            safe_add(stdscr, detail_y, 0, details, width, curses.color_pair(3) if curses.has_colors() else curses.A_BOLD)
        else:
            safe_add(stdscr, detail_y, 0, "No GPUs configured or detected", width, curses.A_BOLD)

        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            break
        if key in (curses.KEY_DOWN, ord("j")):
            selected = min(selected + 1, max(0, len(gpus) - 1))
        elif key in (curses.KEY_UP, ord("k")):
            selected = max(0, selected - 1)
        elif key in (curses.KEY_RIGHT, ord("l"), ord("L")):
            start += timedelta(hours=6)
        elif key in (curses.KEY_LEFT, ord("h"), ord("H")):
            start -= timedelta(hours=6)
        elif key == ord("]"):
            start += timedelta(days=1)
        elif key == ord("["):
            start -= timedelta(days=1)
        elif key in (ord("+"), ord("=")):
            duration = max(timedelta(hours=1), duration / 2)
        elif key in (ord("-"), ord("_")):
            duration = min(DEFAULT_MAX_ADVANCE, duration * 2)
        elif key in (ord("r"), ord("R")):
            continue


def safe_add(stdscr, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
    if y < 0 or x < 0 or width <= 0:
        return
    try:
        stdscr.addnstr(y, x, text, width, attr)
    except Exception:
        pass


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
    positions: list[tuple[int, int, sqlite3.Row]],
    start: datetime,
    end: datetime,
) -> str:
    if not positions:
        return f"GPU {gpu_id}: free for the whole window"
    parts = []
    for _, _, row in positions[:3]:
        parts.append(
            f"id {row['id']} {row['username']} {fmt_span(max(from_utc_text(row['start_time']), start), min(from_utc_text(row['end_time']), end))}"
        )
    extra = "" if len(positions) <= 3 else f"  +{len(positions) - 3} more"
    return f"GPU {gpu_id}: " + " | ".join(parts) + extra


def cmd_reserve(args) -> None:
    store = get_store(args)
    refresh_gpus(store)
    start, end = normalize_window(args)

    if args.gpus and args.count:
        raise ValueError("use either explicit GPU ids or --count, not both")
    if not args.gpus and not args.count:
        raise ValueError("provide GPU ids, or use --count")

    if args.gpus:
        gpu_ids = parse_gpu_list(args.gpus)
    else:
        free = store.find_free_gpus(start, end)
        if len(free) < args.count:
            raise RuntimeError(f"only {len(free)} GPU(s) are free in that interval")
        gpu_ids = free[: args.count]

    reservation_id = store.reserve(getpass.getuser(), gpu_ids, start, end, args.reason)
    print(f"Reserved GPU(s) {','.join(gpu_ids)}")
    print(f"Reservation ID: {reservation_id}")
    print(f"From: {fmt_dt(start)}")
    print(f"To:   {fmt_dt(end)}")


def cmd_cancel(args) -> None:
    store = get_store(args)
    store.init_db()
    user = getpass.getuser()
    store.cancel(args.reservation_id, user, is_admin=(os.geteuid() == 0))
    print(f"Canceled reservation {args.reservation_id}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gpures",
        description="Trust-based GPU reservations for shared Linux servers.",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"data directory, default: {DEFAULT_DATA_DIR}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="initialize the shared reservation database")
    init_p.add_argument("--no-sync", action="store_true", help="do not run nvidia-smi")
    init_p.set_defaults(func=cmd_init)

    status_p = sub.add_parser("status", help="show current and next GPU reservations")
    status_p.set_defaults(func=cmd_status)

    list_p = sub.add_parser("list", help="show all reservations")
    list_p.set_defaults(func=cmd_list)

    calendar_p = sub.add_parser("calendar", help="open an interactive calendar TUI")
    calendar_p.add_argument(
        "--from",
        dest="start",
        default="now",
        help="start time: now, YYYY-MM-DD HH:MM, or local ISO time",
    )
    calendar_p.add_argument(
        "--for",
        dest="duration",
        default="1d",
        help="initial calendar length",
    )
    calendar_p.set_defaults(func=cmd_calendar)

    tui_p = sub.add_parser("tui", help="alias for calendar")
    tui_p.add_argument(
        "--from",
        dest="start",
        default="now",
        help="start time: now, YYYY-MM-DD HH:MM, or local ISO time",
    )
    tui_p.add_argument("--for", dest="duration", default="1d", help="initial calendar length")
    tui_p.set_defaults(func=cmd_calendar)

    gpus_p = sub.add_parser("gpus", help="show configured or detected GPUs")
    gpus_p.set_defaults(func=cmd_gpus)

    reserve_p = sub.add_parser("reserve", help="reserve one or more GPUs")
    reserve_p.add_argument("gpus", nargs="?", help="GPU ids, for example 0 or 0,1")
    reserve_p.add_argument("--count", type=int, help="reserve any N free GPUs")
    reserve_p.add_argument(
        "--from",
        dest="start",
        default="now",
        help="start time: now, YYYY-MM-DD HH:MM, or local ISO time",
    )
    reserve_p.add_argument("--for", dest="duration", help="duration, for example 30m, 4h, 1d")
    reserve_p.add_argument("--until", help="end time: YYYY-MM-DD HH:MM or local ISO time")
    reserve_p.add_argument("--reason", help="optional reason visible to other users")
    reserve_p.set_defaults(func=cmd_reserve)

    mine_p = sub.add_parser("mine", help="show reservations owned by current user")
    mine_p.set_defaults(func=cmd_mine)

    cancel_p = sub.add_parser("cancel", help="cancel your reservation; root can cancel any")
    cancel_p.add_argument("reservation_id", type=int)
    cancel_p.set_defaults(func=cmd_cancel)

    release_p = sub.add_parser("release", help="alias for cancel")
    release_p.add_argument("reservation_id", type=int)
    release_p.set_defaults(func=cmd_cancel)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (RuntimeError, ValueError) as exc:
        print(f"gpures: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

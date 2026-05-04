from __future__ import annotations

import os
from datetime import datetime, timedelta

import getpass

from gpures.constants import DEFAULT_DATA_DIR, DEFAULT_MAX_ADVANCE
from gpures.formatting import format_reservations
from gpures.parsers import parse_duration, parse_gpu_list, parse_local_datetime
from gpures.store import Store, detect_gpus


def normalize_window(
    start_text: str,
    duration: str | None = None,
    until: str | None = None,
) -> tuple[datetime, datetime]:
    now = datetime.now().astimezone().replace(microsecond=0)
    start = parse_local_datetime(start_text, now=now)
    if duration and until:
        raise ValueError("use either --for or --until, not both")
    if not duration and not until:
        raise ValueError("use one of --for or --until")

    if duration:
        end = start + parse_duration(duration)
    else:
        end = parse_local_datetime(until, now=now)

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


def cmd_init(args) -> None:
    store = Store(args.home)
    store.init_db()
    synced = 0 if args.no_sync else store.sync_gpus()
    print(f"Initialized {store.db_path}")
    if synced:
        print(f"Synced {synced} GPU(s)")
    elif not args.no_sync:
        print("No GPUs detected; set GPURES_GPUS or run on a host with nvidia-smi")


def cmd_gpus(args) -> None:
    from gpures.formatting import table

    store = Store(args.home)
    store.refresh()
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
    from gpures.formatting import fmt_dt, status_label, table

    store = Store(args.home)
    store.refresh()
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
    store = Store(args.home)
    store.init_db()
    rows = store.reservation_rows()
    if not rows:
        print("No reservations")
        return
    print(format_reservations(rows))


def cmd_mine(args) -> None:
    store = Store(args.home)
    store.init_db()
    rows = store.reservation_rows(username=getpass.getuser())
    if not rows:
        print("No reservations for current user")
        return
    print(format_reservations(rows))


def cmd_calendar(args) -> None:
    try:
        import curses
    except ImportError as exc:
        raise RuntimeError("calendar TUI requires Python curses support") from exc

    from gpures.tui import run_calendar_tui

    start, end = normalize_calendar_window(args.start, args.duration)
    duration = end - start
    curses.wrapper(run_calendar_tui, args, start, duration)


def cmd_reserve(args) -> None:
    from gpures.formatting import fmt_dt

    store = Store(args.home)
    store.refresh()
    start, end = normalize_window(args.start, args.duration, args.until)

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
    store = Store(args.home)
    store.init_db()
    user = getpass.getuser()
    store.cancel(args.reservation_id, user, is_admin=(os.geteuid() == 0))
    print(f"Canceled reservation {args.reservation_id}")

from __future__ import annotations

import argparse
from pathlib import Path

from gpures.commands import (
    cmd_calendar,
    cmd_cancel,
    cmd_gpus,
    cmd_init,
    cmd_list,
    cmd_mine,
    cmd_reserve,
    cmd_status,
)
from gpures.constants import DEFAULT_DATA_DIR


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

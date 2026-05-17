from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

import curses

import getpass

from gpures.constants import DEFAULT_MAX_ADVANCE
from gpures.formatting import details_for_gpu, fmt_dt, fmt_span, timeline_header
from gpures.store import Store, from_utc_text


def cursor_to_time(cursor_x: int, start: datetime, duration: timedelta, timeline_width: int) -> datetime:
    fraction = cursor_x / max(1, timeline_width)
    return start + timedelta(seconds=fraction * duration.total_seconds())


def time_to_cursor(dt: datetime, start: datetime, duration: timedelta, timeline_width: int) -> int:
    fraction = (dt - start).total_seconds() / max(1, duration.total_seconds())
    return max(0, min(timeline_width - 1, int(fraction * timeline_width)))


def reservation_positions(
    reservations: list[sqlite3.Row],
    start: datetime,
    end: datetime,
    interval_min: int,
    width: int,
) -> dict[str, list[tuple[int, int, sqlite3.Row]]]:
    interval_sec = interval_min * 60
    by_gpu: dict[str, list[tuple[int, int, sqlite3.Row]]] = {}
    for row in reservations:
        reserved_start = max(from_utc_text(row["start_time"]), start)
        reserved_end = min(from_utc_text(row["end_time"]), end)
        if reserved_end <= reserved_start:
            continue
        left = int((reserved_start - start).total_seconds() / interval_sec)
        if left >= width:
            continue
        right = int((reserved_end - start).total_seconds() / interval_sec)
        right = max(left + 1, min(right, width))
        by_gpu.setdefault(row["gpu_id"], []).append((left, right, row))
    return by_gpu


def clip_tui_window(start: datetime) -> datetime:
    now = datetime.now().astimezone().replace(microsecond=0)
    if start < now:
        start = now
    if start > now + DEFAULT_MAX_ADVANCE:
        start = now
    return start


def safe_add(stdscr, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
    if y < 0 or x < 0 or width <= 0:
        return
    try:
        stdscr.addnstr(y, x, text, width, attr)
    except Exception:
        pass


RESOLUTIONS = [5, 10, 30, 60]


def snap_time(dt: datetime, interval_min: int) -> datetime:
    if interval_min <= 0:
        return dt
    ts = dt.timestamp()
    interval_sec = interval_min * 60
    snapped_ts = round(ts / interval_sec) * interval_sec
    return datetime.fromtimestamp(snapped_ts, tz=dt.tzinfo)


def run_calendar_tui(stdscr, args, start: datetime, duration: timedelta) -> None:
    store = Store(args.home)
    scroll = 0
    selected = 0

    mode = "normal"
    reason_text = ""
    status_msg = ""
    res_idx = 2
    cursor_time = snap_time(max(start, datetime.now().astimezone().replace(microsecond=0)), RESOLUTIONS[res_idx])
    selection_start_time = None

    curses.curs_set(0)
    stdscr.nodelay(False)
    if curses.has_colors():
        curses.start_color()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_GREEN)
        curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(5, curses.COLOR_RED, curses.COLOR_BLACK)

    use_utf8 = getattr(sys.stdout, "encoding", "") in (
        "utf-8", "UTF-8", "utf8", "UTF8", "utf_8", "UTF_8"
    )
    if use_utf8:
        chars = {
            "bg": "·",
            "sep": "│",
            "rule": "─",
            "junction": "┼",
            "block_left": "├",
            "block_right": "┤",
            "block_body": "█",
            "block_label": "▓",
            "now_row": "│",
            "now_header": "▲",
            "now_footer": "▼",
            "selection": "░",
        }
    else:
        chars = {
            "bg": ".",
            "sep": "|",
            "rule": "-",
            "junction": "+",
            "block_left": "+",
            "block_right": "+",
            "block_body": "#",
            "block_label": "#",
            "now_row": "|",
            "now_header": "^",
            "now_footer": "v",
            "selection": "~",
        }

    while True:
        store.refresh()
        gpus = store.configured_gpus()

        stdscr.erase()
        height, width = stdscr.getmaxyx()
        timeline_x = 14
        timeline_width = max(10, width - timeline_x - 1)
        start = clip_tui_window(start)
        interval_min = RESOLUTIONS[res_idx]
        visible_end = start + timedelta(minutes=timeline_width * interval_min)
        reservations = store.reservations_between(start, visible_end)
        positions = reservation_positions(reservations, start, visible_end, interval_min, timeline_width)

        selected = min(selected, max(0, len(gpus) - 1))
        scroll = min(scroll, max(0, len(gpus) - 1))

        user_color_map = {}
        if curses.has_colors():
            palette = [
                (curses.COLOR_WHITE, curses.COLOR_RED),
                (curses.COLOR_BLACK, curses.COLOR_GREEN),
                (curses.COLOR_WHITE, curses.COLOR_MAGENTA),
                (curses.COLOR_BLACK, curses.COLOR_YELLOW),
                (curses.COLOR_WHITE, curses.COLOR_CYAN),
                (curses.COLOR_WHITE, curses.COLOR_BLUE),
                (curses.COLOR_BLACK, curses.COLOR_CYAN),
                (curses.COLOR_BLACK, curses.COLOR_MAGENTA),
            ]
            pair_start = 6
            usernames = sorted({row["username"] for row in reservations})
            for idx, username in enumerate(usernames):
                curses.init_pair(pair_start + idx, *palette[idx % len(palette)])
                user_color_map[username] = pair_start + idx

        cursor_time = snap_time(cursor_time, interval_min)
        cursor_time = max(start, min(cursor_time, visible_end))
        cursor_x = int((cursor_time - start).total_seconds() / (interval_min * 60))
        sel_start_x = int((selection_start_time - start).total_seconds() / (interval_min * 60)) if selection_start_time is not None else None

        def _fmt(m: int) -> str:
            if m < 60:
                return f"{m}min"
            h = m // 60
            r = m % 60
            return f"{h}h" if r == 0 else f"{h}:{r:02d}h"
        dot_lbl = _fmt(interval_min)
        hl_lbl = _fmt(interval_min * 5)
        if mode == "normal":
            help_text = f"q quit  dot = {dot_lbl}  s select  h -{hl_lbl} / l +{hl_lbl}  [/ ] 1 day  +/- zoom res  r refresh"
        elif mode == "selecting":
            help_text = f"s confirm  h -{hl_lbl} / l +{hl_lbl}  Esc cancel  r refresh"
        else:
            help_text = "Enter confirm  Esc cancel"

        safe_add(stdscr, 0, 0, f"gpures calendar TUI  @ {fmt_dt(cursor_time)}  {fmt_span(start, visible_end)}", width, curses.A_BOLD)
        safe_add(stdscr, 1, 0, help_text, width)
        safe_add(stdscr, 3, 0, "GPU", 12, curses.A_BOLD)
        safe_add(stdscr, 3, 12, chars["sep"], 1, curses.A_BOLD)
        safe_add(stdscr, 3, timeline_x, timeline_header(start, visible_end, timeline_width), timeline_width, curses.A_BOLD)

        rule = chars["rule"] * 12 + chars["junction"] + chars["rule"] * timeline_width
        safe_add(stdscr, 4, 0, rule, width)

        visible_rows = max(0, height - 8)
        if selected < scroll:
            scroll = selected
        if selected >= scroll + visible_rows:
            scroll = selected - visible_rows + 1

        now = datetime.now().astimezone().replace(microsecond=0)
        now_x = int((now - start).total_seconds() / (interval_min * 60)) if start <= now <= visible_end else None
        now_attr = curses.color_pair(5) if curses.has_colors() else curses.A_BOLD

        for index, gpu in enumerate(gpus[scroll : scroll + visible_rows], start=scroll):
            y = 5 + index - scroll
            attr = curses.A_REVERSE if index == selected else curses.A_NORMAL
            safe_add(stdscr, y, 0, f"{gpu['gpu_id']} {gpu['name']}", 12, attr)
            safe_add(stdscr, y, 12, chars["sep"], 1, attr)

            row_chars = list(chars["bg"] * timeline_width)

            gpu_positions = positions.get(gpu["gpu_id"], [])
            for left, right, reservation in gpu_positions:
                block_width = max(1, right - left)
                if block_width <= 1:
                    row_chars[left] = chars["block_body"]
                else:
                    label = f"#{reservation['id']} {reservation['username']}"
                    row_chars[left] = chars["block_left"]
                    for ci in range(left + 1, min(right - 1, timeline_width)):
                        idx = ci - left
                        row_chars[ci] = label[idx] if idx < len(label) else chars["block_label"]
                    row_chars[min(right - 1, timeline_width - 1)] = chars["block_right"]
                    if right - left <= 2:
                        row_chars[left] = chars["block_body"]
                        if right > 1:
                            row_chars[min(right - 1, timeline_width - 1)] = chars["block_body"]

            if now_x is not None:
                row_chars[now_x] = chars["now_row"]

            if index == selected and gpus:
                if mode == "selecting" and sel_start_x is not None:
                    sel_left = min(sel_start_x, cursor_x)
                    sel_right = max(sel_start_x, cursor_x)
                    for ci in range(sel_left, min(sel_right + 1, timeline_width)):
                        row_chars[ci] = chars["selection"]
                    row_chars[sel_start_x] = chars["sep"]
                    row_chars[cursor_x] = chars["now_footer"]
                else:
                    row_chars[cursor_x] = chars["now_footer"]

            safe_add(stdscr, y, timeline_x, "".join(row_chars), timeline_width)

            for left, right, reservation in gpu_positions:
                block_width = max(1, right - left)
                username = reservation["username"]
                pair_idx = user_color_map.get(username)
                if pair_idx is not None:
                    attr = curses.color_pair(pair_idx)
                    block_text = "".join(row_chars[left:right])
                    safe_add(stdscr, y, timeline_x + left, block_text, block_width, attr)

            if now_x is not None:
                safe_add(stdscr, y, timeline_x + now_x, chars["now_row"], 1, now_attr)

        detail_y = height - 2
        if now_x is not None:
            safe_add(stdscr, 3, timeline_x + now_x, chars["now_header"], 1, now_attr)
        if mode == "reason":
            prompt = f"Reason: {reason_text}"
            if height > 2:
                safe_add(stdscr, detail_y, 0, prompt.ljust(width)[:width], width, curses.color_pair(3) if curses.has_colors() else curses.A_BOLD)
        elif status_msg:
            safe_add(stdscr, detail_y, 0, status_msg.ljust(width)[:width], width, curses.color_pair(3) if curses.has_colors() else curses.A_BOLD)
        elif gpus:
            selected_gpu = gpus[selected]["gpu_id"]
            details = details_for_gpu(selected_gpu, positions.get(selected_gpu, []), start, visible_end)
            safe_add(stdscr, detail_y, 0, details, width, curses.color_pair(3) if curses.has_colors() else curses.A_BOLD)
        else:
            safe_add(stdscr, detail_y, 0, "No GPUs configured or detected", width, curses.A_BOLD)

        if mode == "selecting" and selection_start_time is not None:
            info_y = detail_y - 1
            info = f"Selecting: {fmt_dt(selection_start_time)} -> {fmt_dt(cursor_time)}  ({int((cursor_time - selection_start_time).total_seconds() / 60)}m)"
            safe_add(stdscr, info_y, 0, info, width, curses.A_BOLD)

        if now_x is not None:
            safe_add(stdscr, height - 1, timeline_x + now_x, chars["now_footer"], 1, now_attr)

        stdscr.refresh()
        key = stdscr.getch()

        if key in (ord("q"), ord("Q")):
            break

        if mode == "reason":
            if key in (curses.KEY_ENTER, 10, 13):
                if not gpus:
                    status_msg = "No GPU selected"
                    mode = "normal"
                    continue
                sel_start_time = selection_start_time
                sel_end_time = cursor_time
                if sel_end_time <= sel_start_time:
                    status_msg = "End must be after start"
                    mode = "normal"
                    continue
                now = datetime.now().astimezone().replace(microsecond=0)
                if sel_start_time < now:
                    status_msg = "Start time is in the past"
                    mode = "normal"
                    continue
                horizon_end = now + DEFAULT_MAX_ADVANCE
                if sel_start_time > horizon_end or sel_end_time > horizon_end:
                    status_msg = "Reservation must be within the next 7 days"
                    mode = "normal"
                    continue
                try:
                    rid = store.reserve(
                        getpass.getuser(),
                        [gpus[selected]["gpu_id"]],
                        sel_start_time,
                        sel_end_time,
                        reason_text if reason_text else None,
                    )
                    status_msg = f"Reserved GPU {gpus[selected]['gpu_id']} (id {rid})"
                except RuntimeError as exc:
                    status_msg = str(exc)
                    mode = "selecting"
                    reason_text = ""
                    continue
                mode = "normal"
                selection_start_time = None
                reason_text = ""
            elif key in (27,):
                mode = "normal"
                selection_start_time = None
                reason_text = ""
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                reason_text = reason_text[:-1]
            elif 32 <= key <= 126:
                reason_text += chr(key)
            continue

        if mode == "selecting":
            if key in (ord("s"), ord("S")):
                sel_start_time = selection_start_time
                sel_end_time = cursor_time
                if sel_end_time <= sel_start_time:
                    status_msg = "End must be after start"
                    mode = "normal"
                    continue
                now = datetime.now().astimezone().replace(microsecond=0)
                if sel_start_time < now - timedelta(seconds=60):
                    status_msg = "Start time is in the past"
                    mode = "normal"
                    continue
                mode = "reason"
                reason_text = ""
                continue
            if key in (27,):
                mode = "normal"
                selection_start_time = None
                status_msg = ""
                continue
            if key == curses.KEY_LEFT:
                new_time = cursor_time - timedelta(minutes=interval_min)
                if new_time > selection_start_time:
                    cursor_time = new_time
                elif int((cursor_time - start).total_seconds() / (interval_min * 60)) <= 0:
                    start -= timedelta(minutes=timeline_width * interval_min)
            elif key == curses.KEY_RIGHT:
                cursor_time += timedelta(minutes=interval_min)
            elif key in (curses.KEY_DOWN, ord("j")):
                selected = min(selected + 1, max(0, len(gpus) - 1))
            elif key in (curses.KEY_UP, ord("k")):
                selected = max(0, selected - 1)
            elif key in (ord("h"), ord("H")):
                cursor_time = max(selection_start_time + timedelta(minutes=interval_min), cursor_time - timedelta(minutes=interval_min * 5))
            elif key in (ord("l"), ord("L")):
                cursor_time += timedelta(minutes=interval_min * 5)
            elif key == ord("]"):
                start += timedelta(days=1)
            elif key == ord("["):
                start -= timedelta(days=1)
            elif key in (ord("+"), ord("=")):
                res_idx = max(0, res_idx - 1)
                cursor_time = snap_time(cursor_time, RESOLUTIONS[res_idx])
            elif key in (ord("-"), ord("_")):
                res_idx = min(len(RESOLUTIONS) - 1, res_idx + 1)
                cursor_time = snap_time(cursor_time, RESOLUTIONS[res_idx])
            elif key in (ord("r"), ord("R")):
                continue
            continue

        if mode == "normal":
            if key in (ord("s"), ord("S")):
                if not gpus:
                    status_msg = "No GPU selected"
                    continue
                now = datetime.now().astimezone().replace(microsecond=0)
                if cursor_time < now - timedelta(seconds=60):
                    status_msg = "Cursor position is in the past"
                    continue
                selection_start_time = cursor_time
                mode = "selecting"
                status_msg = ""
                continue
            if key == curses.KEY_LEFT:
                pos = int((cursor_time - start).total_seconds() / (interval_min * 60))
                if pos <= 0:
                    start -= timedelta(minutes=timeline_width * interval_min)
                    cursor_time = snap_time(start, interval_min)
                else:
                    cursor_time -= timedelta(minutes=interval_min)
            elif key == curses.KEY_RIGHT:
                pos = int((cursor_time - start).total_seconds() / (interval_min * 60))
                if pos >= timeline_width - 1:
                    start += timedelta(minutes=timeline_width * interval_min)
                    cursor_time = snap_time(start + timedelta(minutes=(timeline_width - 1) * interval_min), interval_min)
                else:
                    cursor_time += timedelta(minutes=interval_min)
            elif key in (curses.KEY_DOWN, ord("j")):
                selected = min(selected + 1, max(0, len(gpus) - 1))
            elif key in (curses.KEY_UP, ord("k")):
                selected = max(0, selected - 1)
            elif key in (ord("h"), ord("H")):
                cursor_time -= timedelta(minutes=interval_min * 5)
            elif key in (ord("l"), ord("L")):
                cursor_time += timedelta(minutes=interval_min * 5)
            elif key == ord("]"):
                start += timedelta(days=1)
            elif key == ord("["):
                start -= timedelta(days=1)
            elif key in (ord("+"), ord("=")):
                res_idx = max(0, res_idx - 1)
                cursor_time = snap_time(cursor_time, RESOLUTIONS[res_idx])
            elif key in (ord("-"), ord("_")):
                res_idx = min(len(RESOLUTIONS) - 1, res_idx + 1)
                cursor_time = snap_time(cursor_time, RESOLUTIONS[res_idx])
            elif key in (ord("r"), ord("R")):
                continue

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


def safe_add(stdscr, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
    if y < 0 or x < 0 or width <= 0:
        return
    try:
        stdscr.addnstr(y, x, text, width, attr)
    except Exception:
        pass


def run_calendar_tui(stdscr, args, start: datetime, duration: timedelta) -> None:
    store = Store(args.home)
    scroll = 0
    selected = 0

    mode = "normal"
    cursor_x = 0
    selection_start_x = None
    selection_start_time = None
    reason_text = ""
    status_msg = ""

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
        start, end = clip_tui_window(start, duration)
        store.refresh()
        gpus = store.configured_gpus()
        reservations = store.reservations_between(start, end)
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

        stdscr.erase()
        height, width = stdscr.getmaxyx()
        timeline_x = 14
        timeline_width = max(10, width - timeline_x - 1)
        positions = reservation_positions(reservations, start, end, timeline_width)

        if mode == "normal":
            help_text = "q quit  arrows move cursor  s select range  h/l shift 6h  [/ ] shift 1d  +/- zoom  r refresh"
        elif mode == "selecting":
            help_text = "s confirm end  h/l shrink/extend 6h  Esc cancel  r refresh"
        else:
            help_text = "Enter to confirm  Esc cancel"

        safe_add(stdscr, 0, 0, f"gpures calendar TUI  {fmt_span(start, end)}", width, curses.A_BOLD)
        safe_add(stdscr, 1, 0, help_text, width)
        safe_add(stdscr, 3, 0, "GPU", 12, curses.A_BOLD)
        safe_add(stdscr, 3, 12, chars["sep"], 1, curses.A_BOLD)
        safe_add(stdscr, 3, timeline_x, timeline_header(start, end, timeline_width), timeline_width, curses.A_BOLD)

        rule = chars["rule"] * 12 + chars["junction"] + chars["rule"] * timeline_width
        safe_add(stdscr, 4, 0, rule, width)

        visible_rows = max(0, height - 8)
        if selected < scroll:
            scroll = selected
        if selected >= scroll + visible_rows:
            scroll = selected - visible_rows + 1

        now = datetime.now().astimezone().replace(microsecond=0)
        now_x = time_to_cursor(now, start, duration, timeline_width) if start <= now <= (start + duration) else None
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
                cursor_x = max(0, min(timeline_width - 1, cursor_x))
                if mode == "selecting" and selection_start_x is not None:
                    sel_left = min(selection_start_x, cursor_x)
                    sel_right = max(selection_start_x, cursor_x)
                    for ci in range(sel_left, min(sel_right + 1, timeline_width)):
                        row_chars[ci] = chars["selection"]
                    row_chars[selection_start_x] = chars["sep"]
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
            details = details_for_gpu(selected_gpu, positions.get(selected_gpu, []), start, end)
            safe_add(stdscr, detail_y, 0, details, width, curses.color_pair(3) if curses.has_colors() else curses.A_BOLD)
        else:
            safe_add(stdscr, detail_y, 0, "No GPUs configured or detected", width, curses.A_BOLD)

        if mode == "selecting" and selection_start_x is not None:
            info_y = detail_y - 1
            sel_start_time = cursor_to_time(selection_start_x, start, duration, timeline_width)
            sel_end_time = cursor_to_time(cursor_x, start, duration, timeline_width)
            info = f"Selecting: {fmt_dt(sel_start_time)} -> {fmt_dt(sel_end_time)}  ({int((sel_end_time - sel_start_time).total_seconds() / 60)}m)"
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
                sel_end_time = cursor_to_time(cursor_x, start, duration, timeline_width)
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
                cursor_x = 0
                selection_start_x = None
                selection_start_time = None
                reason_text = ""
            elif key in (27,):
                mode = "normal"
                cursor_x = 0
                selection_start_x = None
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
                sel_end_time = cursor_to_time(cursor_x, start, duration, timeline_width)
                if sel_end_time <= sel_start_time:
                    status_msg = "End must be after start"
                    mode = "normal"
                    selection_start_x = None
                    continue
                now = datetime.now().astimezone().replace(microsecond=0)
                if sel_start_time < now - timedelta(seconds=60):
                    status_msg = "Start time is in the past"
                    mode = "normal"
                    selection_start_x = None
                    continue
                mode = "reason"
                reason_text = ""
                continue
            if key in (27,):
                mode = "normal"
                cursor_x = 0
                selection_start_x = None
                selection_start_time = None
                status_msg = ""
                continue
            if key == curses.KEY_LEFT:
                if cursor_x > selection_start_x:
                    cursor_x -= 1
                elif cursor_x == 0:
                    start -= duration / 2
                else:
                    cursor_x = selection_start_x
            elif key == curses.KEY_RIGHT:
                if cursor_x < timeline_width - 1:
                    cursor_x += 1
                else:
                    start += duration / 2
            elif key in (curses.KEY_DOWN, ord("j")):
                selected = min(selected + 1, max(0, len(gpus) - 1))
            elif key in (curses.KEY_UP, ord("k")):
                selected = max(0, selected - 1)
            elif key in (ord("h"), ord("H")):
                six_h_px = max(1, int(timeline_width * 6 / duration.total_seconds() * 3600))
                cursor_x = max(selection_start_x + 1, cursor_x - six_h_px)
            elif key in (ord("l"), ord("L")):
                six_h_px = max(1, int(timeline_width * 6 / duration.total_seconds() * 3600))
                cursor_x = min(timeline_width - 1, cursor_x + six_h_px)
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
            continue

        if mode == "normal":
            if key in (ord("s"), ord("S")):
                if not gpus:
                    status_msg = "No GPU selected"
                    continue
                now = datetime.now().astimezone().replace(microsecond=0)
                cursor_time = cursor_to_time(cursor_x, start, duration, timeline_width)
                if cursor_time < now - timedelta(seconds=60):
                    status_msg = "Cursor position is in the past"
                    continue
                selection_start_x = cursor_x
                selection_start_time = cursor_time
                mode = "selecting"
                status_msg = ""
                continue
            if key == curses.KEY_LEFT:
                if cursor_x > 0:
                    cursor_x -= 1
                else:
                    start -= duration / 2
            elif key == curses.KEY_RIGHT:
                if cursor_x < timeline_width - 1:
                    cursor_x += 1
                else:
                    start += duration / 2
            elif key in (curses.KEY_DOWN, ord("j")):
                selected = min(selected + 1, max(0, len(gpus) - 1))
            elif key in (curses.KEY_UP, ord("k")):
                selected = max(0, selected - 1)
            elif key in (ord("h"), ord("H")):
                start -= timedelta(hours=6)
            elif key in (ord("l"), ord("L")):
                start += timedelta(hours=6)
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

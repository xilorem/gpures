# gpures

Trust-based GPU reservations for shared Linux servers.

Everyone on the machine sees the same schedule. No CUDA overrides,
no permission changes, no job killing. Just a shared calendar.

## Commands

```bash
gpures status
gpures gpus
gpures reserve 0 --for 2h --reason "debug run"
gpures reserve 0,1 --from "2026-04-21 09:00" --until "2026-04-21 12:00"
gpures reserve --count 1 --for 4h
gpures calendar
gpures list
gpures mine
gpures cancel 42
```

Reservations can't overlap on the same GPU and can't start in the past.
Everything must fit within the next 7 days.

`gpures calendar` opens a curses TUI. Works over SSH, zero runtime deps.
Use `--from` and `--for` for a shorter initial window.

TUI keys:

| Key | Action |
|---|---|
| `q` | quit |
| `up` / `down` | select GPU |
| `left` / `right` | move cursor |
| `h` / `l` | shift window 6h |
| `[` / `]` | shift window 1d |
| `s` | start / end reservation range |
| `+` / `-` | zoom in / out |
| `r` | refresh |

## Setup

The shared database lives at:

```text
/var/lib/gpures/reservations.sqlite
```

The Debian package creates a `gpures` system group and makes the data directory
group-writable. Add users:

```bash
sudo usermod -aG gpures alice
```

Group membership takes effect on next login.

## Building the Debian Package

```bash
dpkg-buildpackage -us -uc
```

The `postinst` script creates the data directory and shared DB.

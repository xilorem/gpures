# gpures

`gpures` is a trust-based GPU reservation CLI for shared Linux servers.

It records who reserved which GPU and when, then shows that schedule to every
user on the machine. It does not enforce GPU access, modify CUDA visibility,
change device permissions, or kill jobs.

## Commands

```bash
gpures status
gpures gpus
gpures reserve 0 --for 2h --reason "debug run"
gpures reserve 0,1 --from "2026-04-21 09:00" --until "2026-04-21 12:00"
gpures reserve --count 1 --for 4h
gpures calendar
gpures calendar --from "2026-04-21 09:00" --for 1d
gpures list
gpures mine
gpures cancel 42
```

Reservations cannot overlap on the same GPU, cannot start in the past, and must
start and end within the next seven days. That seven-day period is the full
future calendar that `gpures` exposes.

`gpures calendar` opens an interactive terminal calendar for the next seven
days. It uses Python's standard `curses` module, so it works over SSH and does
not add runtime package dependencies. Shorter initial windows can be requested
with `--from` and `--for`.

TUI keys:

```text
q        quit
up/down  select GPU
left/right or h/l  shift by 6 hours
[ / ]    shift by 1 day
+ / -    zoom in or out
r        refresh
```

## System Install Model

The package installs one `gpures` command for all users and stores reservations
in:

```text
/var/lib/gpures/reservations.sqlite
```

The Debian package creates a system group named `gpures` and makes
`/var/lib/gpures` group-writable. Add users who may reserve GPUs to that group:

```bash
sudo usermod -aG gpures alice
sudo usermod -aG gpures bob
```

Users need a new login session before the group membership applies.


## Debian Package

Build with Debian packaging tools:

```bash
dpkg-buildpackage -us -uc
```

The generated package installs the Python console script and initializes the
shared database directory in `postinst`.

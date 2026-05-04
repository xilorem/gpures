from __future__ import annotations

import contextlib
import fcntl
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gpures.constants import DEFAULT_DB_NAME, DEFAULT_MAX_ADVANCE, ISO_FMT
from gpures.models import Gpu, Reservation
from gpures.parsers import parse_gpu_list


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime(ISO_FMT)


def from_utc_text(value: str) -> datetime:
    return datetime.strptime(value, ISO_FMT).astimezone(
        datetime.now().astimezone().tzinfo
    )


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


def _reservation_from_row(row: sqlite3.Row) -> Reservation:
    return Reservation(
        id=row["id"],
        username=row["username"],
        gpu_ids=row["gpu_ids"].split(",") if isinstance(row["gpu_ids"], str) else [],
        start_time=row["start_time"],
        end_time=row["end_time"],
        reason=row["reason"],
        created_at=row["created_at"],
        canceled_at=row["canceled_at"],
        canceled_by=row["canceled_by"],
    )


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

    def refresh(self) -> None:
        self.init_db()
        self.sync_gpus()

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
                    f"({from_utc_text(row['start_time']).strftime('%Y-%m-%d %H:%M')} "
                    f"to {from_utc_text(row['end_time']).strftime('%Y-%m-%d %H:%M')}, "
                    f"id {row['id']})"
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

    def reservation_rows(self, *, username: str | None = None) -> list[Reservation]:
        where = "WHERE r.username = ?" if username else ""
        params = (username,) if username else ()
        with self.conn() as con:
            rows = con.execute(
                f"""
                SELECT
                    r.id,
                    r.username,
                    r.start_time,
                    r.end_time,
                    r.reason,
                    r.canceled_at,
                    r.canceled_by,
                    r.created_at,
                    GROUP_CONCAT(rg.gpu_id, ',') AS gpu_ids
                FROM reservations r
                JOIN reservation_gpus rg ON rg.reservation_id = r.id
                {where}
                GROUP BY r.id
                ORDER BY r.start_time, r.id
                """,
                params,
            ).fetchall()
        return [_reservation_from_row(row) for row in rows]

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

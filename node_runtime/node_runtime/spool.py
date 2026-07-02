import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class SpoolConflict(ValueError):
    pass


class SpoolCapacityExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class SpoolEvent:
    task_id: str
    sequence: int
    event_type: str
    payload: dict


class EventSpool:
    def __init__(self, path: Path, *, max_bytes: int = 512 * 1024 * 1024) -> None:
        self.path = path
        self.max_bytes = max_bytes
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                task_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (task_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS dispatches (
                task_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL,
                snapshot TEXT NOT NULL,
                state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def _size(self) -> int:
        page_count = self.connection.execute("PRAGMA page_count").fetchone()[0]
        free_pages = self.connection.execute("PRAGMA freelist_count").fetchone()[0]
        page_size = self.connection.execute("PRAGMA page_size").fetchone()[0]
        return (int(page_count) - int(free_pages)) * int(page_size)

    def append(
        self, task_id: str, sequence: int, event_type: str, payload: dict
    ) -> None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode()) > self.max_bytes:
            raise SpoolCapacityExceeded("event is larger than spool capacity")
        existing = self.connection.execute(
            "SELECT event_type, payload FROM events WHERE task_id=? AND sequence=?",
            (task_id, sequence),
        ).fetchone()
        if existing:
            if existing != (event_type, encoded):
                raise SpoolConflict("event sequence conflicts with durable spool")
            return
        if self._size() >= self.max_bytes:
            raise SpoolCapacityExceeded("event spool capacity exceeded")
        with self.connection:
            self.connection.execute(
                "INSERT INTO events(task_id, sequence, event_type, payload) VALUES(?, ?, ?, ?)",
                (task_id, sequence, event_type, encoded),
            )

    def pending(self, task_id: str | None = None) -> list[SpoolEvent]:
        if task_id is None:
            rows = self.connection.execute(
                "SELECT task_id, sequence, event_type, payload FROM events ORDER BY task_id, sequence"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT task_id, sequence, event_type, payload FROM events WHERE task_id=? ORDER BY sequence",
                (task_id,),
            ).fetchall()
        return [SpoolEvent(row[0], row[1], row[2], json.loads(row[3])) for row in rows]

    def acknowledge(self, task_id: str, through_sequence: int) -> None:
        with self.connection:
            self.connection.execute(
                "DELETE FROM events WHERE task_id=? AND sequence<=?",
                (task_id, through_sequence),
            )
            current = int(
                (
                    self.connection.execute(
                        "SELECT value FROM metadata WHERE key='last_acknowledged_event'"
                    ).fetchone()
                    or ["-1"]
                )[0]
            )
            if through_sequence > current:
                self.connection.execute(
                    "INSERT INTO metadata(key, value) VALUES('last_acknowledged_event', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(through_sequence),),
                )

    def reconciliation_range(self) -> tuple[int, int | None, int | None]:
        last_ack = int(
            (
                self.connection.execute(
                    "SELECT value FROM metadata WHERE key='last_acknowledged_event'"
                ).fetchone()
                or ["-1"]
            )[0]
        )
        row = self.connection.execute(
            "SELECT MIN(sequence), MAX(sequence) FROM events"
        ).fetchone()
        return last_ack, row[0], row[1]

    def record_dispatch(self, task_id: str, revision: int, snapshot: dict) -> str:
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        existing = self.connection.execute(
            "SELECT revision, snapshot FROM dispatches WHERE task_id=?", (task_id,)
        ).fetchone()
        if existing:
            if existing == (revision, encoded):
                return "duplicate"
            return "stale" if revision <= existing[0] else "conflict"
        with self.connection:
            self.connection.execute(
                "INSERT INTO dispatches(task_id, revision, snapshot, state) VALUES(?, ?, ?, 'accepted')",
                (task_id, revision, encoded),
            )
        return "accepted"

    def mark_dispatch_state(self, task_id: str, state: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE dispatches SET state=? WHERE task_id=?", (state, task_id)
            )

    def recover_interrupted_dispatches(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT task_id FROM dispatches WHERE state='accepted' ORDER BY task_id"
        ).fetchall()
        task_ids = [str(row[0]) for row in rows]
        if task_ids:
            with self.connection:
                self.connection.executemany(
                    "UPDATE dispatches SET state='interrupted' WHERE task_id=?",
                    [(task_id,) for task_id in task_ids],
                )
        return task_ids

    def close(self) -> None:
        self.connection.close()

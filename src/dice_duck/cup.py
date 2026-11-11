"""Der Würfelbecher: Sessions, Würfe und das hash-verkettete Log in SQLite."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .fairness import HmacStream, client_seed_from, commitment, new_server_seed
from .notation import evaluate, normalize, parse
from .signing import Signer, canonical_json
from .verify import entry_hash, genesis_hash, verify_bundle

RECORD_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id               TEXT PRIMARY KEY,
    title            TEXT,
    created_at       TEXT NOT NULL,
    server_seed      BLOB NOT NULL,
    server_seed_hash TEXT NOT NULL,
    revealed_at      TEXT,
    roll_count       INTEGER NOT NULL DEFAULT 0,
    head_hash        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contributions (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    idx        INTEGER NOT NULL,
    player     TEXT NOT NULL,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, idx)
);
CREATE TABLE IF NOT EXISTS rolls (
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    nonce       INTEGER NOT NULL,
    record_json TEXT NOT NULL,
    entry_hash  TEXT NOT NULL,
    signature   TEXT NOT NULL,
    PRIMARY KEY (session_id, nonce)
);
"""


class NotFoundError(LookupError):
    pass


class ConflictError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class DiceCup:
    def __init__(self, db_path: str | Path, signer: Signer) -> None:
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()
        self.signer = signer

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    # ------------------------------------------------------------------ intern

    def _session_row(self, conn: sqlite3.Connection, sid: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        if row is None:
            raise NotFoundError(f"Session {sid!r} existiert nicht")
        return row

    def _contributions(self, conn: sqlite3.Connection, sid: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT idx, player, value, created_at FROM contributions WHERE session_id = ? ORDER BY idx",
            (sid,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _view(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        contribs = self._contributions(conn, row["id"])
        revealed = row["revealed_at"] is not None
        return {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "status": "revealed" if revealed else "open",
            "server_seed_hash": row["server_seed_hash"],
            "server_seed": row["server_seed"].hex() if revealed else None,
            "revealed_at": row["revealed_at"],
            "contributions": contribs,
            "client_seed": client_seed_from([c["value"] for c in contribs]),
            "seed_locked": row["roll_count"] > 0 or revealed,
            "roll_count": row["roll_count"],
            "head_hash": row["head_hash"],
        }

    @staticmethod
    def _entry(row: sqlite3.Row) -> dict[str, Any]:
        rec = json.loads(row["record_json"])
        return {
            "roll_id": f"{rec['session_id']}-{rec['nonce']}",
            "record": rec,
            "entry_hash": row["entry_hash"],
            "signature": row["signature"],
        }

    # ------------------------------------------------------------- öffentlich

    def create_session(self, title: str | None = None) -> dict[str, Any]:
        sid = uuid.uuid4().hex
        seed = new_server_seed()
        seed_hash = commitment(seed)
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title, created_at, server_seed, server_seed_hash, head_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid, title, _now(), seed, seed_hash, genesis_hash(sid, seed_hash)),
            )
            return self._view(conn, self._session_row(conn, sid))

    def get_session(self, sid: str) -> dict[str, Any]:
        with self._tx() as conn:
            return self._view(conn, self._session_row(conn, sid))

    def add_contribution(self, sid: str, player: str, value: str) -> dict[str, Any]:
        with self._tx() as conn:
            row = self._session_row(conn, sid)
            if row["roll_count"] > 0 or row["revealed_at"]:
                raise ConflictError("Seed ist gesperrt: Beiträge sind nur vor dem ersten Wurf möglich")
            (n,) = conn.execute(
                "SELECT COUNT(*) FROM contributions WHERE session_id = ?", (sid,)
            ).fetchone()
            conn.execute(
                "INSERT INTO contributions (session_id, idx, player, value, created_at) VALUES (?, ?, ?, ?, ?)",
                (sid, n, player, value, _now()),
            )
            return self._view(conn, row)

    def roll(self, sid: str, expr: str, actor: str, reason: str | None = None) -> dict[str, Any]:
        terms = parse(expr)  # vor dem Lock validieren; wirft NotationError
        with self._tx() as conn:
            row = self._session_row(conn, sid)
            if row["revealed_at"]:
                raise ConflictError("Session ist beendet (server_seed aufgedeckt)")
            nonce = row["roll_count"]
            client_seed = client_seed_from([c["value"] for c in self._contributions(conn, sid)])
            result = evaluate(terms, HmacStream(row["server_seed"], client_seed, nonce))
            record = {
                "version": RECORD_VERSION,
                "session_id": sid,
                "nonce": nonce,
                "expr": expr,
                "normalized": normalize(terms),
                "actor": actor,
                "reason": reason,
                "created_at": _now(),
                "server_seed_hash": row["server_seed_hash"],
                "client_seed": client_seed,
                "prev_hash": row["head_hash"],
                "result": result,
                "total": result["total"],
            }
            h = entry_hash(record)
            sig = self.signer.sign(canonical_json(record))
            conn.execute(
                "INSERT INTO rolls (session_id, nonce, record_json, entry_hash, signature) VALUES (?, ?, ?, ?, ?)",
                (sid, nonce, canonical_json(record).decode("utf-8"), h, sig),
            )
            conn.execute(
                "UPDATE sessions SET roll_count = ?, head_hash = ? WHERE id = ?",
                (nonce + 1, h, sid),
            )
        return {"roll_id": f"{sid}-{nonce}", "record": record, "entry_hash": h, "signature": sig}

    def list_rolls(self, sid: str) -> list[dict[str, Any]]:
        with self._tx() as conn:
            self._session_row(conn, sid)
            rows = conn.execute(
                "SELECT * FROM rolls WHERE session_id = ? ORDER BY nonce", (sid,)
            ).fetchall()
            return [self._entry(r) for r in rows]

    def get_roll(self, sid: str, nonce: int) -> dict[str, Any]:
        with self._tx() as conn:
            self._session_row(conn, sid)
            row = conn.execute(
                "SELECT * FROM rolls WHERE session_id = ? AND nonce = ?", (sid, nonce)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Wurf {nonce} in Session {sid!r} existiert nicht")
            return self._entry(row)

    def reveal(self, sid: str) -> dict[str, Any]:
        with self._tx() as conn:
            row = self._session_row(conn, sid)
            if not row["revealed_at"]:
                conn.execute("UPDATE sessions SET revealed_at = ? WHERE id = ?", (_now(), sid))
            return self._view(conn, self._session_row(conn, sid))

    def export(self, sid: str) -> dict[str, Any]:
        # Session und Würfe in EINER Transaktion lesen, sonst passt der Snapshot ggf. nicht zusammen
        with self._tx() as conn:
            session = self._view(conn, self._session_row(conn, sid))
            rows = conn.execute(
                "SELECT * FROM rolls WHERE session_id = ? ORDER BY nonce", (sid,)
            ).fetchall()
            rolls = [self._entry(r) for r in rows]
        return {
            "format": "dice-duck-export-v1",
            "public_key": self.signer.public_key_b64,
            "session": session,
            "rolls": rolls,
        }

    def verify(self, sid: str) -> dict[str, Any]:
        return verify_bundle(self.export(sid))

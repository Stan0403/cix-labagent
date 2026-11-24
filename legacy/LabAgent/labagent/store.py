"""One SQLite transaction per state transition, with revision checks and append-only audit."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import DomainError, NewSession, State, now


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_session ON events(session_id, sequence);
                CREATE TABLE IF NOT EXISTS messages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    role TEXT NOT NULL, content TEXT NOT NULL, at TEXT NOT NULL
                );
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, request: NewSession) -> State:
        state = State(title=request.title, mode=request.mode)
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (state.id, state.model_dump_json(), now()))
            self._event(db, state.id, "session.created", {"mode": state.mode})
        return state

    def _read(self, db, session_id) -> State:
        row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise DomainError("实验不存在", 404)
        return State.model_validate_json(row["state"])

    def get(self, session_id) -> State:
        with self.connect() as db:
            return self._read(db, session_id)

    def list(self) -> list[dict]:
        with self.connect() as db:
            return [json.loads(row["state"]) for row in db.execute(
                "SELECT state FROM sessions ORDER BY updated_at DESC LIMIT 100")]

    def _event(self, db, session_id, kind, payload):
        db.execute("INSERT INTO events(session_id,at,kind,payload) VALUES (?,?,?,?)",
                   (session_id, now(), kind, json.dumps(payload, ensure_ascii=False)))

    def mutate(self, session_id, kind, payload, function, expected_revision=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._read(db, session_id)
            if expected_revision is not None and expected_revision != state.revision:
                raise DomainError("状态已更新，请刷新后重试，避免重复或过期操作", 409)
            result = function(state)
            state.revision += 1
            state.updated_at = now()
            db.execute("UPDATE sessions SET state=?, updated_at=? WHERE id=?",
                       (state.model_dump_json(), state.updated_at, state.id))
            self._event(db, session_id, kind, {"input": payload, "result": result,
                                              "revision": state.revision, "step": state.step})
        return state, result

    def audit(self, session_id, kind, payload):
        with self.connect() as db:
            self._read(db, session_id)
            self._event(db, session_id, kind, payload)

    def events(self, session_id, after=0, limit=200) -> list[dict]:
        with self.connect() as db:
            self._read(db, session_id)
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in db.execute(
                "SELECT * FROM events WHERE session_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (session_id, after, limit))]

    def message(self, session_id, role, content):
        with self.connect() as db:
            self._read(db, session_id)
            db.execute("INSERT INTO messages(session_id,role,content,at) VALUES (?,?,?,?)",
                       (session_id, role, content, now()))

    def messages(self, session_id, limit=40):
        with self.connect() as db:
            self._read(db, session_id)
            rows = db.execute("SELECT * FROM messages WHERE session_id=? ORDER BY sequence DESC LIMIT ?",
                              (session_id, limit)).fetchall()
            return [dict(row) for row in reversed(rows)]

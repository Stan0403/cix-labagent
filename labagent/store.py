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

    def create(self, request: NewSession, plan=None) -> State:
        state = State(title=plan["title"] if plan else request.title, mode=request.mode, plan=plan,
                      automation=request.automation)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._pause_running(db, "已开始另一个实验，本实验已暂停；确认现场后可恢复。")
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (state.id, state.model_dump_json(
                exclude={"automation"} if state.automation=="workflow" else None), now()))
            self._event(db, state.id, "session.created", {"mode": state.mode})
        return state

    def _pause(self, db, state, reason):
        if state.status != "running":
            return
        state.status, state.pause_reason = "paused", reason
        state.revision += 1
        state.updated_at = now()
        db.execute("UPDATE sessions SET state=?, updated_at=? WHERE id=?",
                   (state.model_dump_json(), state.updated_at, state.id))
        self._event(db, state.id, "session.paused", {
            "input": {"kind": "pause", "automatic": True, "note": reason},
            "result": {"accepted": True, "message": reason},
            "revision": state.revision, "step": state.step})

    def _pause_running(self, db, reason, exclude=""):
        rows = db.execute("SELECT state FROM sessions WHERE id!=? AND json_extract(state,'$.status')='running'",
                          (exclude,)).fetchall()
        for row in rows:
            self._pause(db, State.model_validate_json(row["state"]), reason)
        return len(rows)

    def pause_running(self, reason):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._pause_running(db, reason)

    def review(self, session_id, previous_id=None):
        """Opening history or refreshing never restarts an experiment implicitly."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._read(db, session_id)
            if previous_id and previous_id != session_id:
                previous = db.execute("SELECT state FROM sessions WHERE id=?", (previous_id,)).fetchone()
                if previous:
                    self._pause(db, State.model_validate_json(previous["state"]),
                                "已离开本实验页面，自动指导已暂停；确认现场后可恢复。")
            self._pause(db, state, "重新打开或刷新实验，已暂停自动指导；确认现场后可恢复。")
            return state

    def delete(self, session_id, expected_revision):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._read(db, session_id)
            if state.revision != expected_revision:
                raise DomainError("实验状态已变化，请重新确认删除", 409)
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_runs'").fetchone():
                db.execute("DELETE FROM agent_runs WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM events WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM sessions WHERE id=?", (session_id,))

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
            before_status = state.status
            result = function(state)
            if before_status != "running" and state.status == "running" and result.get("accepted") is True:
                self._pause_running(db, "已恢复另一个实验，本实验已暂停；确认现场后可恢复。", state.id)
            state.revision += 1
            state.updated_at = now()
            db.execute("UPDATE sessions SET state=?, updated_at=? WHERE id=?",
                       (state.model_dump_json(exclude={"automation"} if state.automation=="workflow" else None), state.updated_at, state.id))
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

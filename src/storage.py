"""SQLite persistence for task state and run history."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite
from loguru import logger

from .models import TaskDef, TaskRun, TaskState, TaskStatus


class Storage:
    """Persistent storage for task state and run history."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        self._conn = await aiosqlite.connect(str(self.db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._init_db()

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def _init_db(self):
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                name TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                definition TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_runs (
                id TEXT PRIMARY KEY,
                task_name TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                output TEXT,
                error TEXT,
                attempt INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (task_name) REFERENCES tasks(name)
            );
            CREATE INDEX IF NOT EXISTS idx_runs_task ON task_runs(task_name);
            CREATE INDEX IF NOT EXISTS idx_runs_status ON task_runs(status);
            CREATE TABLE IF NOT EXISTS task_state (
                task_name TEXT PRIMARY KEY,
                last_run_at TEXT,
                last_status TEXT,
                last_output TEXT,
                last_error TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0
            );
        """)
        await self._conn.commit()

    # ── Tasks ──────────────────────────────────────────────

    async def upsert_task(self, task: TaskDef) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO tasks (name, enabled, definition) VALUES (?, ?, ?)",
            (task.name, True, task.model_dump_json())
        )
        await self._conn.commit()

    async def get_task(self, name: str) -> Optional[TaskDef]:
        row = await self._conn.execute_fetchall(
            "SELECT definition FROM tasks WHERE name = ?", (name,)
        )
        if row:
            return TaskDef.model_validate_json(row[0][0])
        return None

    async def list_tasks(self) -> list[TaskDef]:
        rows = await self._conn.execute_fetchall(
            "SELECT name, enabled, definition FROM tasks ORDER BY name"
        )
        result = []
        for row in rows:
            task = TaskDef.model_validate_json(row[2])
            task.name = row[0]  # name from DB takes precedence
            result.append(task)
        return result

    async def delete_task(self, name: str) -> bool:
        cur = await self._conn.execute("DELETE FROM tasks WHERE name = ?", (name,))
        await self._conn.commit()
        return cur.rowcount > 0

    async def set_task_enabled(self, name: str, enabled: bool) -> None:
        await self._conn.execute(
            "UPDATE tasks SET enabled = ? WHERE name = ?", (int(enabled), name)
        )
        await self._conn.commit()

    # ── Task Runs ──────────────────────────────────────────

    async def create_run(self, task_name: str, attempt: int = 1) -> str:
        run_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "INSERT INTO task_runs (id, task_name, status, started_at, attempt) VALUES (?, ?, ?, ?, ?)",
            (run_id, task_name, TaskStatus.running.value, now, attempt)
        )
        await self._conn.commit()
        return run_id

    async def finish_run(self, run_id: str, status: TaskStatus, output: str = "", error: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "UPDATE task_runs SET status = ?, finished_at = ?, output = ?, error = ? WHERE id = ?",
            (status.value, now, output[:10000], error[:5000], run_id)
        )
        await self._conn.commit()

    async def get_recent_runs(self, task_name: str, limit: int = 10) -> list[TaskRun]:
        rows = await self._conn.execute_fetchall(
            "SELECT * FROM task_runs WHERE task_name = ? ORDER BY started_at DESC LIMIT ?",
            (task_name, limit)
        )
        return [self._row_to_run(r) for r in rows]

    # ── Task State ─────────────────────────────────────────

    async def get_state(self, task_name: str) -> Optional[TaskState]:
        rows = await self._conn.execute_fetchall(
            "SELECT * FROM task_state WHERE task_name = ?", (task_name,)
        )
        if rows:
            return self._row_to_state(rows[0])
        return None

    async def update_state(self, task_name: str, status: TaskStatus, output: str = "", error: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        is_success = status == TaskStatus.success

        existing = await self.get_state(task_name)
        consec = 0 if is_success else (existing.consecutive_failures + 1 if existing else 1)

        await self._conn.execute("""
            INSERT INTO task_state (task_name, last_run_at, last_status, last_output, last_error, consecutive_failures)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_name) DO UPDATE SET
                last_run_at = excluded.last_run_at,
                last_status = excluded.last_status,
                last_output = excluded.last_output,
                last_error = excluded.last_error,
                consecutive_failures = excluded.consecutive_failures
        """, (task_name, now, status.value, output[:5000], error[:2000], consec))
        await self._conn.commit()

    async def list_states(self) -> list[TaskState]:
        rows = await self._conn.execute_fetchall("SELECT * FROM task_state ORDER BY task_name")
        return [self._row_to_state(r) for r in rows]

    # ── Helpers ────────────────────────────────────────────

    def _row_to_run(self, row) -> TaskRun:
        return TaskRun(
            id=row["id"],
            task_name=row["task_name"],
            status=TaskStatus(row["status"]),
            started_at=self._parse_dt(row["started_at"]),
            finished_at=self._parse_dt(row["finished_at"]),
            output=row["output"] or "",
            error=row["error"] or "",
            attempt=row["attempt"],
        )

    def _row_to_state(self, row) -> TaskState:
        return TaskState(
            task_name=row["task_name"],
            enabled=bool(row.get("enabled", True)),
            last_run_at=self._parse_dt(row["last_run_at"]),
            last_status=TaskStatus(row["last_status"]) if row["last_status"] else None,
            last_output=row["last_output"] or "",
            last_error=row["last_error"] or "",
            consecutive_failures=row["consecutive_failures"],
        )

    def _parse_dt(self, s: Optional[str]) -> Optional[datetime]:
        if s:
            try:
                return datetime.fromisoformat(s)
            except (ValueError, TypeError):
                pass
        return None

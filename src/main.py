"""FastAPI entry point for the Agent Task Scheduler."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from loguru import logger

from .config import load_config
from .models import TaskDef, TaskRun
from .scheduler import AgentScheduler


scheduler: Optional[AgentScheduler] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    config_path = os.getenv("SCHEDULER_CONFIG")
    scheduler = AgentScheduler(config_path)
    await scheduler.start()
    yield
    await scheduler.stop()


app = FastAPI(
    title="Agent Task Scheduler",
    description="Lightweight scheduler for agent tasks with dependency chaining",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Task Management ───────────────────────────────────────

@app.get("/tasks")
async def list_tasks():
    """List all tasks with their status."""
    if not scheduler:
        raise HTTPException(503, "Scheduler not initialized")
    return await scheduler.get_status()


@app.post("/tasks")
async def create_task(task: TaskDef):
    """Create or update a task."""
    if not scheduler:
        raise HTTPException(503, "Scheduler not initialized")
    await scheduler.add_task(task)
    return {"status": "ok", "name": task.name}


@app.delete("/tasks/{name}")
async def delete_task(name: str):
    """Delete a task."""
    if not scheduler:
        raise HTTPException(503, "Scheduler not initialized")
    ok = await scheduler.remove_task(name)
    if not ok:
        raise HTTPException(404, f"Task '{name}' not found")
    return {"status": "deleted", "name": name}


# ── Execution ─────────────────────────────────────────────

@app.post("/tasks/{name}/run")
async def run_task(name: str):
    """Trigger a task immediately."""
    if not scheduler:
        raise HTTPException(503, "Scheduler not initialized")
    run = await scheduler.run_task_now(name)
    if run is None:
        raise HTTPException(404, f"Task '{name}' not found or dependencies not met")
    return {
        "status": run.status.value,
        "run_id": run.id,
        "output": (run.output or "")[:2000],
        "error": run.error,
    }


# ── Health ────────────────────────────────────────────────

@app.get("/health")
async def health():
    if not scheduler:
        return {"status": "starting"}
    status = await scheduler.get_status()
    return {
        "status": "running" if scheduler._running else "stopped",
        "tasks": status["task_count"],
    }

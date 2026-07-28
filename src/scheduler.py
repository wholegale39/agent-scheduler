"""Core scheduler — wraps APScheduler with dependency chains and task orchestration."""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from .config import load_config
from .deliver import Deliverer
from .executor import Executor
from .models import (
    DeliveryTarget,
    RetryConfig,
    TaskDef,
    TaskRun,
    TaskState,
    TaskStatus,
)
from .storage import Storage


def parse_schedule(schedule_str: str, timezone: str = "Asia/Shanghai"):
    """Parse a schedule string into an APScheduler trigger.

    Supports:
    - Cron: "0 18 * * 1-5"
    - Interval: "every 30m", "every 2h", "every 1d"
    """
    s = schedule_str.strip().lower()
    tz = timezone

    if s.startswith("every "):
        parts = s[6:].strip().split()
        if len(parts) < 1:
            raise ValueError(f"Invalid interval schedule: {schedule_str}")

        value = int(parts[0])
        unit = parts[1] if len(parts) > 1 else "m"

        if unit.startswith("m"):
            return IntervalTrigger(minutes=value, timezone=tz)
        elif unit.startswith("h"):
            return IntervalTrigger(hours=value, timezone=tz)
        elif unit.startswith("d"):
            return IntervalTrigger(days=value, timezone=tz)
        else:
            raise ValueError(f"Unknown interval unit: {unit}")
    else:
        # Cron expression
        return CronTrigger.from_crontab(schedule_str, timezone=tz)


class AgentScheduler:
    """Main scheduler that manages tasks, dependencies, and execution."""

    def __init__(self, config_path: Optional[str] = None):
        self.config = load_config(config_path)
        self._aps = AsyncIOScheduler()
        self._storage = Storage(self.config.db_path)
        self._executor = Executor()
        self._deliverer = Deliverer()
        self._running = False

        # In-memory task registry with dependency tracking
        self._tasks: dict[str, TaskDef] = {}
        self._dependents: dict[str, list[str]] = defaultdict(list)  # task → list of downstream tasks

    # ── Lifecycle ──────────────────────────────────────────

    async def start(self):
        logger.info("Starting Agent Scheduler...")
        await self._storage.connect()
        await self._load_tasks()
        self._register_all()
        self._aps.start()
        self._running = True
        logger.info(f"Agent Scheduler started with {len(self._tasks)} tasks")

    async def stop(self):
        self._running = False
        self._aps.shutdown(wait=False)
        await self._executor.close()
        await self._deliverer.close()
        await self._storage.close()
        logger.info("Agent Scheduler stopped")

    # ── Task Loading ───────────────────────────────────────

    async def _load_tasks(self):
        """Load tasks from YAML files in tasks_dir."""
        tasks_dir = Path(self.config.tasks_dir)
        if not tasks_dir.exists():
            logger.warning(f"Tasks directory not found: {tasks_dir}")
            return

        for yaml_file in sorted(tasks_dir.glob("*.yaml")) + sorted(tasks_dir.glob("*.yml")):
            logger.info(f"Loading tasks from {yaml_file}")
            with open(yaml_file) as f:
                data = yaml.safe_load(f)

            if not data:
                continue

            # Single task or list
            items = data if isinstance(data, list) else [data]
            for item in items:
                try:
                    task = TaskDef(**item)
                    self._tasks[task.name] = task
                    await self._storage.upsert_task(task)
                    logger.debug(f"  Loaded task: {task.name}")
                except Exception as e:
                    logger.error(f"  Failed to load task from {yaml_file}: {e}")

        # Build dependency graph
        self._dependents.clear()
        for name, task in self._tasks.items():
            for dep in task.depends_on:
                self._dependents[dep].append(name)

    async def add_task(self, task: TaskDef) -> None:
        """Add or update a task at runtime."""
        self._tasks[task.name] = task
        await self._storage.upsert_task(task)
        # Rebuild dependents
        self._dependents.clear()
        for name, t in self._tasks.items():
            for dep in t.depends_on:
                self._dependents[dep].append(name)
        # Register in scheduler
        self._register_task(task)
        logger.info(f"Task '{task.name}' added and scheduled")

    async def remove_task(self, name: str) -> bool:
        """Remove a task."""
        if name in self._tasks:
            del self._tasks[name]
            self._dependents.pop(name, None)
            # Remove from other dependents lists
            for deps in self._dependents.values():
                if name in deps:
                    deps.remove(name)
            try:
                self._aps.remove_job(f"task_{name}")
            except Exception:
                pass
            await self._storage.delete_task(name)
            logger.info(f"Task '{name}' removed")
            return True
        return False

    # ── APScheduler Registration ───────────────────────────

    def _register_all(self):
        for name, task in self._tasks.items():
            self._register_task(task)

    def _register_task(self, task: TaskDef):
        """Register a single task with APScheduler."""
        if not task.schedule:
            logger.debug(f"Task '{task.name}' has no schedule (manual only)")
            return

        trigger = parse_schedule(task.schedule, task.timezone)
        job_id = f"task_{task.name}"

        # Remove existing job if any
        try:
            self._aps.remove_job(job_id)
        except Exception:
            pass

        self._aps.add_job(
            self._on_schedule_trigger,
            trigger=trigger,
            id=job_id,
            name=task.name,
            args=[task.name],
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info(f"  Scheduled '{task.name}': {task.schedule}")

    # ── Execution ──────────────────────────────────────────

    async def _on_schedule_trigger(self, task_name: str):
        """Called by APScheduler when a task's schedule fires."""
        if not self._running:
            return
        await self._check_and_run(task_name)

    async def run_task_now(self, task_name: str) -> Optional[TaskRun]:
        """Manually trigger a task run."""
        return await self._check_and_run(task_name, force=True)

    async def _check_and_run(self, task_name: str, force: bool = False) -> Optional[TaskRun]:
        """Check dependencies, then run a task (and its downstream chain)."""
        task = self._tasks.get(task_name)
        if not task:
            logger.warning(f"Task '{task_name}' not found")
            return None

        state = await self._storage.get_state(task_name)

        # Check if task is enabled
        if state and not state.enabled and not force:
            logger.debug(f"Task '{task_name}' is disabled, skipping")
            return None

        # Check day-of-week filter
        if task.run_on_days is not None and not force:
            today = datetime.now(timezone.utc).astimezone()
            # Convert Python weekday (0=Mon) to our convention
            if today.weekday() not in task.run_on_days:
                logger.debug(f"Task '{task_name}' skipped: not a scheduled day")
                return None

        # Check dependencies
        if not force and task.depends_on:
            for dep_name in task.depends_on:
                dep_state = await self._storage.get_state(dep_name)
                if dep_state is None or dep_state.last_status != TaskStatus.success:
                    logger.info(f"Task '{task_name}' waiting: dependency '{dep_name}' not yet succeeded")
                    return None

        # Run with retry
        return await self._run_with_retry(task)

    async def _run_with_retry(self, task: TaskDef) -> TaskRun:
        """Run a task with retry logic."""
        retry = task.retry
        last_run: Optional[TaskRun] = None

        for attempt in range(1, retry.max_attempts + 1):
            run = await self._execute_once(task, attempt)
            last_run = run

            if run.status == TaskStatus.success:
                await self._on_task_done(task, run)
                return run

            if attempt < retry.max_attempts:
                delay = retry.delay_seconds * (retry.backoff_multiplier ** (attempt - 1))
                logger.warning(f"Task '{task.name}' failed (attempt {attempt}/{retry.max_attempts}), "
                              f"retrying in {delay:.0f}s: {run.error}")
                await asyncio.sleep(delay)

        # All attempts failed
        await self._on_task_done(task, last_run)
        return last_run

    async def _execute_once(self, task: TaskDef, attempt: int) -> TaskRun:
        """Execute a single attempt of a task."""
        run_id = await self._storage.create_run(task.name, attempt)
        run = TaskRun(
            id=run_id,
            task_name=task.name,
            status=TaskStatus.running,
            started_at=datetime.now(timezone.utc),
            attempt=attempt,
        )

        logger.info(f"Running task '{task.name}' (attempt {attempt})...")

        output, error = await self._executor.execute(task)

        if error:
            run.status = TaskStatus.failed
            run.error = error
            run.output = output
        else:
            run.status = TaskStatus.success
            run.output = output

        run.finished_at = datetime.now(timezone.utc)
        await self._storage.finish_run(run_id, run.status, output, error)
        await self._storage.update_state(task.name, run.status, output, error)

        return run

    async def _on_task_done(self, task: TaskDef, run: TaskRun):
        """Handle completion of a task run."""
        logger.info(f"Task '{task.name}': {run.status.value}" + 
                   (f" ({run.error})" if run.error else ""))

        # Deliver result
        targets = list(task.deliver)
        if run.status == TaskStatus.failed and self.config.alert_deliver:
            state = await self._storage.get_state(task.name)
            if state and state.consecutive_failures >= self.config.alert_on_failure_after:
                logger.warning(f"Task '{task.name}' has {state.consecutive_failures} consecutive failures, alerting")
                targets.extend(self.config.alert_deliver)

        if targets:
            await self._deliverer.deliver(run, targets, task.name)

        # Trigger downstream tasks
        if run.status == TaskStatus.success:
            downstream = self._dependents.get(task.name, [])
            for dep_name in downstream:
                logger.info(f"  Triggering downstream task '{dep_name}'")
                asyncio.create_task(self._check_and_run(dep_name))

    # ── Status ─────────────────────────────────────────────

    async def get_status(self) -> dict:
        tasks = []
        for name, task in self._tasks.items():
            state = await self._storage.get_state(name)
            tasks.append({
                "name": name,
                "type": task.type.value,
                "schedule": task.schedule,
                "depends_on": task.depends_on,
                "dependents": self._dependents.get(name, []),
                "enabled": state.enabled if state else True,
                "last_status": state.last_status.value if state and state.last_status else None,
                "last_run_at": state.last_run_at.isoformat() if state and state.last_run_at else None,
                "consecutive_failures": state.consecutive_failures if state else 0,
            })
        return {
            "running": self._running,
            "task_count": len(self._tasks),
            "tasks": tasks,
        }

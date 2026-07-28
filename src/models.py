"""Data models for the Agent Task Scheduler."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    """What kind of task this is."""
    prompt = "prompt"         # Send prompt to an LLM agent
    script = "script"         # Run a shell command/script
    webhook = "webhook"       # POST to a URL


class TaskStatus(str, Enum):
    """Current status of a task run."""
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"
    skipped = "skipped"       # Dependency failed, so this is skipped
    cancelled = "cancelled"


class DeliveryTarget(BaseModel):
    """Where to deliver the result."""
    platform: str             # "feishu", "telegram", "webhook"
    target: str               # Chat ID, webhook URL, etc.


class LLMConfig(BaseModel):
    """LLM provider configuration for prompt tasks."""
    provider: str = "openai"          # "openai", "openrouter", "deepseek", "custom"
    model: str = "gpt-4o-mini"
    base_url: Optional[str] = None     # For custom endpoints
    api_key_env: str = "LLM_API_KEY"   # Env var name holding the key
    max_tokens: int = 4096
    temperature: float = 0.7
    system_prompt: Optional[str] = None


class RetryConfig(BaseModel):
    """Retry on failure."""
    max_attempts: int = 3
    delay_seconds: int = 30
    backoff_multiplier: float = 2.0


class TaskDef(BaseModel):
    """A single task definition."""
    name: str
    description: Optional[str] = None
    type: TaskType = TaskType.prompt

    # Schedule
    schedule: Optional[str] = None       # "0 18 * * 1-5", "every 30m", or None for manual
    timezone: str = "Asia/Shanghai"

    # Dependencies
    depends_on: list[str] = Field(default_factory=list)

    # Task payload (type-specific)
    prompt: Optional[str] = None         # For prompt tasks
    script: Optional[str] = None         # For script tasks (shell command)
    webhook_url: Optional[str] = None    # For webhook tasks
    webhook_method: str = "POST"
    webhook_body: Optional[dict] = None

    # Execution
    llm: Optional[LLMConfig] = None      # For prompt tasks (defaults from global config)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    timeout_seconds: int = 300

    # Delivery
    deliver: list[DeliveryTarget] = Field(default_factory=list)

    # Filter: only run when conditions are met
    run_on_days: Optional[list[int]] = None  # Day of week (0=Mon, 6=Sun). None=every day


class TaskRun(BaseModel):
    """Record of a single execution of a task."""
    id: str
    task_name: str
    status: TaskStatus
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    output: Optional[str] = None
    error: Optional[str] = None
    attempt: int = 1


class TaskState(BaseModel):
    """Persistent state of a task (keeps track of last run, etc.)."""
    task_name: str
    enabled: bool = True
    last_run_at: Optional[datetime] = None
    last_status: Optional[TaskStatus] = None
    last_output: Optional[str] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0


class SchedulerConfig(BaseModel):
    """Top-level config for the scheduler service."""
    db_path: str = "data/scheduler.db"
    tasks_dir: str = "tasks"
    default_llm: LLMConfig = Field(default_factory=LLMConfig)
    api_host: str = "0.0.0.0"
    api_port: int = 8767
    log_level: str = "INFO"
    alert_on_failure_after: int = 3  # Alert after N consecutive failures
    alert_deliver: list[DeliveryTarget] = Field(default_factory=list)

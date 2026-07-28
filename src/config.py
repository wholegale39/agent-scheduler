"""Configuration loader."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from loguru import logger

from .models import LLMConfig, SchedulerConfig


def load_config(path: str | Path | None = None) -> SchedulerConfig:
    """Load config from YAML file, with env var overrides."""
    paths_to_try = []
    if path:
        paths_to_try.append(Path(path))
    paths_to_try.extend([
        Path("config.yaml"),
        Path("config.yml"),
        Path("/etc/agent-scheduler/config.yaml"),
    ])

    config_data = {}
    for p in paths_to_try:
        if p.exists():
            logger.info(f"Loading config from {p}")
            with open(p) as f:
                config_data = yaml.safe_load(f) or {}
            break
    else:
        logger.warning("No config file found, using defaults + env vars")

    # Override from env vars
    config_data.setdefault("default_llm", {})
    if os.getenv("LLM_API_KEY"):
        config_data["default_llm"]["api_key_env"] = "LLM_API_KEY"
    if os.getenv("LLM_BASE_URL"):
        config_data["default_llm"]["base_url"] = os.getenv("LLM_BASE_URL")
    if os.getenv("LLM_MODEL"):
        config_data["default_llm"]["model"] = os.getenv("LLM_MODEL")
    if os.getenv("LLM_PROVIDER"):
        config_data["default_llm"]["provider"] = os.getenv("LLM_PROVIDER")
    if os.getenv("SCHEDULER_DB_PATH"):
        config_data["db_path"] = os.getenv("SCHEDULER_DB_PATH")
    if os.getenv("SCHEDULER_TASKS_DIR"):
        config_data["tasks_dir"] = os.getenv("SCHEDULER_TASKS_DIR")
    if os.getenv("API_HOST"):
        config_data["api_host"] = os.getenv("API_HOST")
    if os.getenv("API_PORT"):
        config_data["api_port"] = int(os.getenv("API_PORT"))

    return SchedulerConfig(**config_data)

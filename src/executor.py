"""Task executor — runs prompt tasks via LLM, script tasks via shell."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from typing import Optional

import httpx
from loguru import logger

from .models import LLMConfig, TaskDef, TaskType


class Executor:
    """Runs tasks and returns (output, error)."""

    def __init__(self):
        self._http_client: Optional[httpx.AsyncClient] = None

    async def ensure_client(self):
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=120.0)

    async def close(self):
        if self._http_client:
            await self._http_client.aclose()

    async def execute(self, task: TaskDef) -> tuple[str, str]:
        """Execute a task. Returns (output, error)."""
        self._http_client = httpx.AsyncClient(timeout=task.timeout_seconds + 30)

        try:
            if task.type == TaskType.prompt:
                return await self._execute_prompt(task)
            elif task.type == TaskType.script:
                return await self._execute_script(task)
            elif task.type == TaskType.webhook:
                return await self._execute_webhook(task)
            else:
                return "", f"Unknown task type: {task.type}"
        finally:
            await self._http_client.aclose()
            self._http_client = None

    async def _execute_prompt(self, task: TaskDef) -> tuple[str, str]:
        """Send prompt to an LLM and return the response."""
        llm = task.llm or LLMConfig()
        api_key = os.getenv(llm.api_key_env) or os.getenv("LLM_API_KEY")
        if not api_key:
            return "", f"API key not found in env var '{llm.api_key_env}'"

        base_url = (llm.base_url or self._resolve_base_url(llm.provider)).rstrip("/")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        messages = []
        if llm.system_prompt:
            messages.append({"role": "system", "content": llm.system_prompt})
        messages.append({"role": "user", "content": task.prompt})

        payload = {
            "model": llm.model,
            "messages": messages,
            "max_tokens": llm.max_tokens,
            "temperature": llm.temperature,
            "stream": False,
        }

        logger.info(f"LLM call: {llm.provider}/{llm.model} for task '{task.name}'")

        try:
            resp = await self._http_client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage", {})
            logger.info(f"LLM response: {usage.get('total_tokens', '?')} tokens")
            return content, ""
        except httpx.TimeoutException:
            return "", f"LLM request timed out after {task.timeout_seconds}s"
        except Exception as e:
            return "", f"LLM request failed: {e}"

    async def _execute_script(self, task: TaskDef) -> tuple[str, str]:
        """Run a shell command or script."""
        if not task.script:
            return "", "No script command specified"

        logger.info(f"Running script for task '{task.name}': {task.script[:100]}")

        try:
            proc = await asyncio.create_subprocess_shell(
                task.script,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=task.timeout_seconds
                )
            except asyncio.TimeoutError:
                proc.kill()
                return "", f"Script timed out after {task.timeout_seconds}s"

            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                return out, err or f"Exit code: {proc.returncode}"

            return out, ""
        except Exception as e:
            return "", f"Script execution failed: {e}"

    async def _execute_webhook(self, task: TaskDef) -> tuple[str, str]:
        """POST to a webhook URL."""
        if not task.webhook_url:
            return "", "No webhook URL specified"

        logger.info(f"Calling webhook for task '{task.name}': {task.webhook_url}")

        try:
            method = task.webhook_method.upper()
            kwargs = {}
            if task.webhook_body:
                kwargs["json"] = task.webhook_body

            resp = await self._http_client.request(method, task.webhook_url, **kwargs)
            text = resp.text[:5000]
            if resp.status_code >= 400:
                return "", f"Webhook returned {resp.status_code}: {text[:500]}"
            return text, ""
        except Exception as e:
            return "", f"Webhook failed: {e}"

    def _resolve_base_url(self, provider: str) -> str:
        urls = {
            "openai": "https://api.openai.com/v1",
            "openrouter": "https://openrouter.ai/api/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "anthropic": "https://api.anthropic.com/v1",
        }
        return urls.get(provider, "https://api.openai.com/v1")

"""Multi-platform delivery for task results."""

from __future__ import annotations

import json
import os
from typing import Optional

import httpx
from loguru import logger

from .models import DeliveryTarget, TaskRun, TaskStatus


class Deliverer:
    """Sends task results to configured destinations."""

    def __init__(self):
        self._http: Optional[httpx.AsyncClient] = None

    async def ensure_client(self):
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15.0)

    async def close(self):
        if self._http:
            await self._http.aclose()

    async def deliver(
        self,
        run: TaskRun,
        targets: list[DeliveryTarget],
        task_name: str,
    ) -> list[dict]:
        """Deliver a run result to all targets. Returns per-target results."""
        await self.ensure_client()
        results = []

        for target in targets:
            try:
                result = await self._deliver_one(run, target, task_name)
                results.append({"target": target.model_dump(), "ok": True, "result": result})
            except Exception as e:
                logger.error(f"Delivery to {target.platform}:{target.target} failed: {e}")
                results.append({"target": target.model_dump(), "ok": False, "error": str(e)})

        return results

    async def _deliver_one(self, run: TaskRun, target: DeliveryTarget, task_name: str) -> str:
        platform = target.platform.lower()

        if platform == "webhook":
            return await self._deliver_webhook(run, target)
        elif platform == "feishu":
            return await self._deliver_feishu(run, target, task_name)
        elif platform == "telegram":
            return await self._deliver_telegram(run, target)
        elif platform == "stdout":
            return self._deliver_stdout(run, task_name)
        else:
            return await self._deliver_webhook(run, target)  # fallback to webhook

    def _format_message(self, run: TaskRun, task_name: str) -> str:
        """Build a compact message for the run result."""
        status_emoji = {
            TaskStatus.success: "✅",
            TaskStatus.failed: "❌",
            TaskStatus.skipped: "⏭",
            TaskStatus.cancelled: "🚫",
            TaskStatus.running: "🔄",
        }.get(run.status, "❓")

        lines = [
            f"{status_emoji} {task_name}",
            f"  Status: {run.status.value}",
        ]
        if run.started_at:
            lines.append(f"  Started: {run.started_at.strftime('%H:%M:%S')}")
        if run.finished_at:
            lines.append(f"  Finished: {run.finished_at.strftime('%H:%M:%S')}")
        if run.attempt > 1:
            lines.append(f"  Attempt: #{run.attempt}")

        output = (run.output or "").strip()
        error = (run.error or "").strip()
        if output:
            # Truncate long output
            if len(output) > 500:
                output = output[:497] + "..."
            lines.append(f"")
            lines.append(output)
        if error and run.status == TaskStatus.failed:
            if len(error) > 300:
                error = error[:297] + "..."
            lines.append(f"")
            lines.append(f"Error: {error}")

        return "\n".join(lines)

    async def _deliver_webhook(self, run: TaskRun, target: DeliveryTarget) -> str:
        """POST result as JSON to a webhook URL."""
        payload = {
            "event": "task_run",
            "task_name": run.task_name,
            "status": run.status.value,
            "output": run.output,
            "error": run.error,
            "attempt": run.attempt,
        }
        resp = await self._http.post(target.target, json=payload)
        resp.raise_for_status()
        return f"webhook {resp.status_code}"

    async def _deliver_feishu(self, run: TaskRun, target: DeliveryTarget, task_name: str) -> str:
        """Send message to Feishu via webhook (bot webhook URL in target)."""
        msg = self._format_message(run, task_name)
        payload = {
            "msg_type": "text",
            "content": json.dumps({"text": msg}),
        }
        resp = await self._http.post(target.target, json=payload)
        resp.raise_for_status()
        return f"feishu {resp.status_code}"

    async def _deliver_telegram(self, run: TaskRun, target: DeliveryTarget) -> str:
        """Send message to Telegram. target.target format: bot_token/chat_id"""
        parts = target.target.split("/", 1)
        if len(parts) != 2:
            return f"invalid telegram target format, expected bot_token/chat_id"
        bot_token, chat_id = parts
        msg = self._format_message(run, run.task_name)
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = await self._http.post(url, json={
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "Markdown",
        })
        resp.raise_for_status()
        return f"telegram {resp.status_code}"

    def _deliver_stdout(self, run: TaskRun, task_name: str) -> str:
        """Print to stdout (for CLI mode)."""
        msg = self._format_message(run, task_name)
        print(msg)
        return "stdout"

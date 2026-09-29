from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from .ingestion import process_ingestion, publish_reviewed_content


logger = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task] = {}


def _task_done(key: str, task: asyncio.Task) -> None:
    _tasks.pop(key, None)
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:  # noqa: BLE001
        logger.exception("后台入库任务异常：%s", key)


def _schedule(key: str, factory: Callable[[], Coroutine[Any, Any, None]]) -> bool:
    current = _tasks.get(key)
    if current and not current.done():
        return False
    task = asyncio.create_task(factory(), name=key)
    _tasks[key] = task
    task.add_done_callback(lambda completed: _task_done(key, completed))
    return True


def schedule_ingestion(job_id: str) -> bool:
    return _schedule(f"ingestion:{job_id}", lambda: process_ingestion(job_id))


def schedule_reviewed_publish(job_id: str) -> bool:
    return _schedule(f"reviewed:{job_id}", lambda: publish_reviewed_content(job_id))


def cancel_job(job_id: str) -> bool:
    cancelled = False
    for prefix in ("ingestion", "reviewed"):
        task = _tasks.pop(f"{prefix}:{job_id}", None)
        if task and not task.done():
            task.cancel()
            cancelled = True
    return cancelled


async def shutdown_jobs() -> None:
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()

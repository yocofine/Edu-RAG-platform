from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import wraps
from typing import AsyncIterator, Callable, TypeVar

from ..db import engine


_LOCAL_LOCKS: dict[str, asyncio.Lock] = {}
F = TypeVar("F", bound=Callable)


def _try_acquire_mysql_lock(name: str):
    connection = engine.raw_connection()
    cursor = None
    acquired = False
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT GET_LOCK(%s, 0)", (name,))
        row = cursor.fetchone()
        acquired = bool(row and int(row[0] or 0) == 1)
    finally:
        if cursor is not None:
            cursor.close()
        if not acquired:
            connection.close()
    if not acquired:
        return None
    return connection


def _release_mysql_lock(connection, name: str) -> None:
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT RELEASE_LOCK(%s)", (name,))
        cursor.fetchone()
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


@asynccontextmanager
async def serial_operation(name: str, timeout_seconds: int = 21600) -> AsyncIterator[None]:
    """Serialize work locally and, on MySQL, across API processes/pods."""
    lock = _LOCAL_LOCKS.setdefault(name, asyncio.Lock())
    async with lock:
        if engine.dialect.name not in {"mysql", "mariadb"}:
            yield
            return

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        connection = None
        while connection is None:
            connection = await asyncio.to_thread(_try_acquire_mysql_lock, name)
            if connection is not None:
                break
            if loop.time() >= deadline:
                raise TimeoutError(f"等待串行任务锁超时：{name}")
            await asyncio.sleep(0.25)
        try:
            yield
        finally:
            await asyncio.shield(asyncio.to_thread(_release_mysql_lock, connection, name))


def serialized(name: str, timeout_seconds: int = 21600):
    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            async with serial_operation(name, timeout_seconds):
                return await func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator

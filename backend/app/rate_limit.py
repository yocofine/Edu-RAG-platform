from collections import defaultdict, deque
from time import monotonic

from fastapi import HTTPException, Request


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window = window_seconds
        self.events: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = monotonic()
        bucket = self.events[key]
        while bucket and bucket[0] <= now - self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        bucket.append(now)


auth_limiter = SlidingWindowLimiter(10)
chat_limiter = SlidingWindowLimiter(60)
upload_limiter = SlidingWindowLimiter(10)


def limit_auth(request: Request) -> None:
    auth_limiter.check(request.client.host if request.client else "unknown")


def limit_chat(request: Request) -> None:
    chat_limiter.check(request.client.host if request.client else "unknown")


def limit_upload(request: Request) -> None:
    upload_limiter.check(request.client.host if request.client else "unknown")


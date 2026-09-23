import asyncio
from collections import defaultdict

from fastapi import WebSocket


class EventHub:
    def __init__(self) -> None:
        self.connections: dict[int, set[WebSocket]] = defaultdict(set)
        self.lock = asyncio.Lock()

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self.lock:
            self.connections[user_id].add(websocket)

    async def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        async with self.lock:
            self.connections[user_id].discard(websocket)

    async def broadcast(self, payload: dict) -> None:
        stale: list[tuple[int, WebSocket]] = []
        for user_id, sockets in list(self.connections.items()):
            for websocket in list(sockets):
                try:
                    await websocket.send_json(payload)
                except Exception:
                    stale.append((user_id, websocket))
        for user_id, websocket in stale:
            await self.disconnect(user_id, websocket)


event_hub = EventHub()


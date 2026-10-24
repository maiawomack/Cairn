"""Dashboard log: agent messages fan out to every open browser over Server Sent Events.

Messages use the CLAUDE.md shape: { from, type, data, time }. publish() is safe to
call from any thread (the orchestrator runs in a background thread).
"""

import asyncio
import threading
from collections import deque
from datetime import datetime, timezone

HISTORY_SIZE = 200


class EventHub:
    def __init__(self):
        self._lock = threading.Lock()
        self._history = deque(maxlen=HISTORY_SIZE)
        self._subscribers = []  # (event loop, queue)

    def publish(self, source, type_, data=None):
        message = {
            "from": source,
            "type": type_,
            "data": data or {},
            "time": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._history.append(message)
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            loop.call_soon_threadsafe(queue.put_nowait, message)
        return message

    async def subscribe(self):
        """Yield recent history, then every new message."""
        queue = asyncio.Queue()
        entry = (asyncio.get_running_loop(), queue)
        with self._lock:
            backlog = list(self._history)
            self._subscribers.append(entry)
        try:
            for message in backlog:
                yield message
            while True:
                yield await queue.get()
        finally:
            with self._lock:
                self._subscribers.remove(entry)


hub = EventHub()

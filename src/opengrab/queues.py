"""Multiple named download queues, each with its own concurrency (M11m).

Pure module: tracks which download ids sit in which queue, which are active, and
which should start next. No threads, no I/O.
"""
from __future__ import annotations

DEFAULT_QUEUE = "Main"


def clean_name(name) -> str:
    text = " ".join(str(name or "").split())
    if not text:
        raise ValueError("queue name is required")
    return text


def clean_limit(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"bad max_active: {value!r}") from None
    if number < 1:
        raise ValueError(f"max_active must be at least 1: {value!r}")
    return number


class NamedQueue:
    def __init__(self, name, max_active=3, paused=False):
        self.name = clean_name(name)
        self.max_active = clean_limit(max_active)
        self.paused = bool(paused)
        self.items: list[str] = []
        self.active: set[str] = set()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "max_active": self.max_active,
            "paused": self.paused,
            "items": list(self.items),
        }


class QueueManager:
    def __init__(self, default_max_active=3):
        self.queues: dict[str, NamedQueue] = {}
        self.create(DEFAULT_QUEUE, default_max_active)

    def _key(self, name) -> str:
        wanted = clean_name(name).lower()
        for key in self.queues:
            if key.lower() == wanted:
                return key
        raise KeyError(name)

    def get(self, name) -> NamedQueue:
        return self.queues[self._key(name)]

    def names(self) -> list:
        return list(self.queues)

    def create(self, name, max_active=3) -> NamedQueue:
        queue = NamedQueue(name, max_active)
        if any(key.lower() == queue.name.lower() for key in self.queues):
            raise ValueError(f"queue already exists: {queue.name!r}")
        self.queues[queue.name] = queue
        return queue

    def set_limit(self, name, max_active) -> None:
        self.get(name).max_active = clean_limit(max_active)

    def pause(self, name, paused=True) -> None:
        self.get(name).paused = bool(paused)

    def rename(self, old, new) -> NamedQueue:
        key = self._key(old)
        if key == DEFAULT_QUEUE:
            raise ValueError("the default queue cannot be renamed")
        target = clean_name(new)
        if any(k.lower() == target.lower() and k != key for k in self.queues):
            raise ValueError(f"queue already exists: {target!r}")
        queue = self.queues.pop(key)
        queue.name = target
        self.queues[target] = queue
        return queue

    def remove(self, name) -> None:
        key = self._key(name)
        if key == DEFAULT_QUEUE:
            raise ValueError("the default queue cannot be removed")
        queue = self.queues.pop(key)
        main = self.queues[DEFAULT_QUEUE]
        for item in queue.items:
            main.items.append(item)
            if item in queue.active:
                main.active.add(item)

    def queue_of(self, item_id):
        for queue in self.queues.values():
            if item_id in queue.items:
                return queue.name
        return None

    def add(self, item_id, queue=None) -> str:
        item = str(item_id)
        if self.queue_of(item) is not None:
            raise ValueError(f"item already queued: {item!r}")
        target = self.get(queue or DEFAULT_QUEUE)
        target.items.append(item)
        return target.name

    def discard(self, item_id) -> bool:
        item = str(item_id)
        for queue in self.queues.values():
            if item in queue.items:
                queue.items.remove(item)
                queue.active.discard(item)
                return True
        return False

    def move(self, item_id, queue) -> str:
        item = str(item_id)
        source = self.queue_of(item)
        if source is None:
            raise KeyError(item_id)
        target = self.get(queue)
        was_active = item in self.queues[source].active
        self.discard(item)
        target.items.append(item)
        if was_active:
            target.active.add(item)
        return target.name

    def start(self, item_id) -> None:
        item = str(item_id)
        source = self.queue_of(item)
        if source is None:
            raise KeyError(item_id)
        self.queues[source].active.add(item)

    def finish(self, item_id) -> bool:
        return self.discard(item_id)

    def runnable(self) -> list:
        """Ids to start now: FIFO per queue, skipping paused queues and full queues."""
        result = []
        for queue in self.queues.values():
            if queue.paused:
                continue
            free = queue.max_active - len(queue.active)
            for item in queue.items:
                if free <= 0:
                    break
                if item not in queue.active:
                    result.append(item)
                    free -= 1
        return result

    def to_dict(self) -> dict:
        return {"queues": [q.to_dict() for q in self.queues.values()]}

    @classmethod
    def from_dict(cls, data) -> "QueueManager":
        manager = cls()
        raw_queues = data.get("queues") if isinstance(data, dict) else None
        for raw in raw_queues or []:
            if not isinstance(raw, dict):
                continue
            try:
                name = clean_name(raw.get("name"))
                limit = clean_limit(raw.get("max_active", 3))
            except ValueError:
                continue
            try:
                queue = manager.get(name)
                queue.max_active = limit
            except KeyError:
                queue = manager.create(name, limit)
            queue.paused = bool(raw.get("paused", False))
            for item in raw.get("items") or []:
                if isinstance(item, str) and manager.queue_of(item) is None:
                    queue.items.append(item)
        return manager
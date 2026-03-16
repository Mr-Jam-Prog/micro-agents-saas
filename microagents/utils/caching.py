import threading
from collections import OrderedDict
from typing import Any, Optional, Dict

class ThreadSafeLRUCache:
    """
    A thread-safe Least Recently Used (LRU) cache.
    Provides dictionary-like interface with a fixed capacity.
    """
    def __init__(self, max_size: int):
        self.max_size = max_size
        self._cache = OrderedDict()
        self._lock = threading.Lock()

    def __getitem__(self, key: Any) -> Any:
        with self._lock:
            val = self._cache[key]
            self._cache.move_to_end(key)
            return val

    def __setitem__(self, key: Any, value: Any) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            while len(self._cache) > self.max_size:
                self._cache.popitem(last=False)

    def __contains__(self, key: Any) -> bool:
        with self._lock:
            return key in self._cache

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return default

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def pop(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            return self._cache.pop(key, default)

    def keys(self):
        with self._lock:
            return list(self._cache.keys())

    def values(self):
        with self._lock:
            return list(self._cache.values())

    def items(self):
        with self._lock:
            return list(self._cache.items())

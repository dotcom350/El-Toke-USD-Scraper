import json
import os
import threading
from datetime import datetime, timezone
from typing import Optional


class RateCache:
    """Thread-safe in-memory cache backed by a JSON file on disk."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._data: Optional[dict] = None

    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        with self._lock:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)

    def set(self, data: dict) -> None:
        with self._lock:
            self._data = data
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def set_error(self, message: str) -> None:
        with self._lock:
            if self._data is not None:
                self._data["last_error"] = message
                self._data["last_error_at"] = datetime.now(timezone.utc).isoformat()

    def get(self) -> Optional[dict]:
        with self._lock:
            return self._data

    def last_scrape_at(self) -> Optional[str]:
        with self._lock:
            return self._data.get("scraped_at") if self._data else None

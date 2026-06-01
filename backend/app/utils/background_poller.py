from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.repositories.api_repository import APIHotelRepository

logger = logging.getLogger(__name__)


class BackgroundPoller:
    """Daemon thread that proactively refreshes hotel cache entries before TTL expires."""

    def __init__(
        self,
        repository: APIHotelRepository,
        poll_interval: int = 50,
        refresh_threshold: float = 0.2,
    ) -> None:
        self._repo = repository
        self._poll_interval = poll_interval
        self._refresh_threshold = refresh_threshold
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="hotel-cache-poller",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "BackgroundPoller started (interval=%ds, threshold=%.0f%%)",
            self._poll_interval,
            self._refresh_threshold * 100,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("BackgroundPoller stopped")

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop_event.wait(timeout=self._poll_interval):
            self._refresh_expiring_keys()

    def _refresh_expiring_keys(self) -> None:
        cache = self._repo._cache
        ttl = self._repo._cache_ttl
        if ttl <= 0 or not cache:
            return

        threshold_seconds = ttl * self._refresh_threshold
        now = time.monotonic()

        for key, (_hotels, fetched_at) in list(cache.items()):
            remaining = ttl - (now - fetched_at)
            if remaining > threshold_seconds:
                continue
            iata_code = None if key == "__all__" else key
            logger.debug("Proactive refresh for cache key '%s'", key)
            try:
                del cache[key]
                self._repo.get_all_hotels(iata_code)
            except Exception as exc:
                logger.warning("Proactive refresh failed for key '%s': %s", key, exc)

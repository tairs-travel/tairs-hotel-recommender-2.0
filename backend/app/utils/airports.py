from __future__ import annotations

import logging
import math
import time

from app.repositories.airline_repository import APIAirlineRepository

logger = logging.getLogger(__name__)


class _AirportCache:
    """Airport metadata from airlines API"""

    def __init__(self, ttl: int = 3600) -> None:
        self._ttl = ttl
        self._data: dict[str, dict] = {}
        self._fetched_at: float = 0.0
        self._repo = APIAirlineRepository(cache_ttl=ttl)

    def _is_stale(self) -> bool:
        if self._ttl <= 0:
            return True
        return time.monotonic() - self._fetched_at >= self._ttl

    def _refresh(self) -> None:
        airlines = self._repo.get_all_airlines()
        resolved: dict[str, dict] = {}

        for airline in airlines:
            code = airline.iata_code.upper()
            if code in resolved:
                continue
            lat, lng = airline.lat, airline.lng
            if not lat and not lng:
                logger.warning("Missing coordinates for airport: %s — skipped", code)
                continue
            resolved[code] = {
                "code": code,
                "country": airline.country,
                "lat": lat,
                "lng": lng,
            }

        self._data = resolved
        self._fetched_at = time.monotonic()
        logger.info("Airport cache refreshed — %d airports resolved", len(resolved))

    def get(self, code: str) -> dict | None:
        if self._is_stale():
            self._refresh()
        return self._data.get(code.upper())


_cache = _AirportCache(ttl=3600)


def get_airport(code: str) -> dict | None:
    """Case-insensitive IATA lookup. Returns airport metadata or None."""
    return _cache.get(code)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometres between two lat/lng points."""
    R = 6_371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(min(a, 1.0)))


def get_airports_within_radius(iata_code: str, radius_km: float) -> list[str]:
    """Return IATA codes of airports within *radius_km* of *iata_code*.

    The result is sorted by ascending distance. The source airport itself is
    excluded. Returns an empty list when the source airport is unknown or when
    the cache contains no neighbours within the radius.
    """
    if _cache._is_stale():
        _cache._refresh()

    primary = iata_code.upper()
    source = _cache._data.get(primary)
    if source is None:
        logger.debug("get_airports_within_radius: unknown airport %s", primary)
        return []

    nearby: list[tuple[float, str]] = []
    for code, info in _cache._data.items():
        if code == primary:
            continue
        dist = _haversine_km(source["lat"], source["lng"], info["lat"], info["lng"])
        if dist <= radius_km:
            nearby.append((dist, code))

    nearby.sort()
    logger.debug(
        "get_airports_within_radius(%s, %.0f km): %d neighbour(s) found",
        primary, radius_km, len(nearby),
    )
    return [code for _, code in nearby]

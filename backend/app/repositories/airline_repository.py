from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

import requests

from app.config.settings import Config
from app.domain.airline_models import Airline

logger = logging.getLogger(__name__)


class AirlineRepository(ABC):

    @abstractmethod
    def get_all_airlines(self) -> list[Airline]:
        """Return all available airlines."""

    def get_by_display_name(self, display_name: str) -> Airline | None:
        """Return the first airline whose display_name matches, or None."""
        for airline in self.get_all_airlines():
            if airline.display_name == display_name:
                return airline
        return None


class APIAirlineRepository(AirlineRepository):
    """Fetches airlines from the external REST API with simple TTL caching."""

    def __init__(self, config=None, cache_ttl: int = 60) -> None:
        self._config = config or Config
        self._cache_ttl = cache_ttl
        self._airlines: list[Airline] | None = None
        self._cache_ts: float = 0.0

    # ------------------------------------------------------------------ public

    def get_all_airlines(self) -> list[Airline]:
        now = time.monotonic()
        if (
            self._airlines is not None
            and self._cache_ttl > 0
            and now - self._cache_ts < self._cache_ttl
        ):
            return self._airlines

        self._airlines = self._fetch_and_map()
        self._cache_ts = time.monotonic()
        return self._airlines

    # --------------------------------------------------------------- internals

    def _fetch_and_map(self) -> list[Airline]:
        url = self._config.AIRLINES_API_URL
        if not url:
            logger.warning("AIRLINES_API_URL is not configured — skipping fetch")
            return []

        headers: dict[str, str] = {}
        if self._config.AIRLINES_API_KEY:
            headers["x-api-key"] = self._config.AIRLINES_API_KEY

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            logger.error("Airlines API request failed: %s", exc)
            return []
        except ValueError as exc:
            logger.error("Airlines API returned invalid JSON: %s", exc)
            return []

        raw_airlines = payload.get("airlines") if isinstance(payload, dict) else None
        if not isinstance(raw_airlines, list):
            logger.warning("Airlines API: unexpected response shape — %s", type(payload))
            return []

        airlines: list[Airline] = []

        for entry in raw_airlines:
            airline_name = entry.get("airline", "")
            destinations = entry.get("destinations")

            if not airline_name or not isinstance(destinations, list):
                logger.warning("Airlines API: malformed entry skipped — %s", entry)
                continue

            for dest in destinations:
                try:
                    if dest.get("status") != "active":
                        continue

                    coach_price = dest.get("coach_price")
                    if not coach_price:
                        continue

                    iata_code = dest.get("iata", "").strip().upper()
                    if not iata_code:
                        logger.warning(
                            "Airlines API: missing iata for %s — skipped", airline_name
                        )
                        continue

                    lat = dest.get("latitude", dest.get("lat"))
                    lng = dest.get("longitude", dest.get("lng"))
                    airlines.append(
                        Airline(
                            name=airline_name,
                            iata_code=iata_code,
                            coach_price=float(coach_price),
                            country=str(dest.get("country", "")),
                            lat=float(lat) if lat is not None else 0.0,
                            lng=float(lng) if lng is not None else 0.0,
                        )
                    )
                except (TypeError, ValueError) as exc:
                    logger.warning(
                        "Airlines API: malformed destination in %s — %s", airline_name, exc
                    )

        return airlines

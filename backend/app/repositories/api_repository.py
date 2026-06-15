from __future__ import annotations

import logging
import time
from typing import Any

import requests

from app.config.settings import Config
from app.domain.models import Hotel, RoomAvailability, RoomPrices
from app.repositories.base_repository import HotelRepository

logger = logging.getLogger(__name__)

_PRIORITY_MAP: dict[str, str] = {
    "1": "1",
    "2": "2",
    "3": "3",
    "4": "4",
}


class APIHotelRepository(HotelRepository):
    """Fetches hotels from the external REST API with optional per-IATA TTL caching.

    Set cache_ttl=0 to disable caching entirely (every call hits the API).
    """

    def __init__(self, config=None, cache_ttl: int = 60) -> None:
        self._config = config or Config
        self._cache_ttl = cache_ttl
        # { cache_key: (hotels_list, fetched_at_timestamp) }
        self._cache: dict[str, tuple[list, float]] = {}

    # ------------------------------------------------------------------ public

    def get_all_hotels(self, iata_code: str | None = None) -> list[Hotel]:
        cache_key = iata_code.upper() if iata_code else "__all__"

        if self._cache_ttl > 0 and cache_key in self._cache:
            hotels, fetched_at = self._cache[cache_key]
            if time.monotonic() - fetched_at < self._cache_ttl:
                return hotels

        raw_items = self._fetch(iata_code)
        hotels = [h for item in raw_items if (h := self._map(item)) is not None]

        if self._cache_ttl > 0:
            self._cache[cache_key] = (hotels, time.monotonic())

        return hotels

    def get_hotel_by_id(self, hotel_id: str) -> Hotel | None:
        for hotel in self.get_all_hotels():
            if hotel.id == hotel_id:
                return hotel
        return None

    # --------------------------------------------------------------- internals

    def _fetch(self, iata_code: str | None = None) -> list[dict]:
        url = self._config.HOTELS_API_URL
        params: dict[str, str] = {}
        if iata_code:
            params["iata_code"] = iata_code.upper()

        headers: dict[str, str] = {}
        if self._config.HOTELS_API_KEY:
            headers["x-api-key"] = self._config.HOTELS_API_KEY

        try:
            response = requests.get(url, params=params, headers=headers, timeout=5)
            response.raise_for_status()
            payload: Any = response.json()
        except requests.RequestException as exc:
            logger.error("Hotels API request failed: %s", exc)
            return []
        except ValueError as exc:
            logger.error("Hotels API returned invalid JSON: %s", exc)
            return []

        if isinstance(payload, list):
            return payload

        if isinstance(payload, dict):
            for key in ("data", "hotels", "results"):
                if isinstance(payload.get(key), list):
                    return payload[key]

        logger.warning("Unexpected Hotels API response shape: %s", type(payload))
        return []

    @staticmethod
    def _safe_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _map(self, item: dict) -> Hotel | None:
        """Map availability API shape (company, latitude, single_rooms, etc.).

        Room counts come only from ``availability`` (per-type or available_rooms).
        Top-level ``single_rooms`` / ``double_rooms`` are prices, not inventory.
        """
        try:
            avail = item.get("availability") or {}

            rooms = RoomAvailability(
                single=self._safe_int(avail.get("single_rooms")),
                double=self._safe_int(avail.get("double_rooms")),
                triple=self._safe_int(avail.get("triple_rooms")),
                quadruple=self._safe_int(avail.get("quadruple_rooms")),
            )

            total_avail = rooms.single + rooms.double + rooms.triple + rooms.quadruple
            if total_avail == 0:
                fallback = self._safe_int(avail.get("available_rooms"))
                if fallback > 0:
                    logger.debug(
                        "Hotel %s: no per-type availability; using available_rooms=%d as single",
                        item.get("id"),
                        fallback,
                    )
                    rooms = RoomAvailability(
                        single=fallback,
                        double=0,
                        triple=0,
                        quadruple=0,
                    )
                else:
                    return None

            prices = RoomPrices(
                single=self._safe_float(item.get("single_rooms")),
                double=self._safe_float(item.get("double_rooms")),
                triple=self._safe_float(item.get("triple_rooms")),
                quadruple=self._safe_float(item.get("quadruple_rooms")),
            )

            raw_amenities = item.get("amenities") or {}
            amenities = (
                {k: bool(v) for k, v in raw_amenities.items()}
                if isinstance(raw_amenities, dict)
                else {}
            )

            pet_friendly = bool(
                item.get("pet_friendly")
                or item.get("petfriendly")
                or amenities.get("pet_friendly")
                or amenities.get("petfriendly")
            )

            raw_meals = item.get("meals") or {}
            if isinstance(raw_meals, dict):
                meals = {
                    "breakfast": raw_meals.get("breakfast") in (1, "1", True),
                    "lunch": raw_meals.get("lunch") in (1, "1", True),
                    "dinner": raw_meals.get("dinner") in (1, "1", True),
                }
            else:
                meals = {"breakfast": False, "lunch": False, "dinner": False}

            raw_category = item.get("category") or []
            categories = (
                list(raw_category)
                if isinstance(raw_category, list)
                else [str(raw_category)]
            )
            all_inclusive = "all_inclusive" in categories

            lat = item.get("latitude", item.get("lat"))
            lng = item.get("longitude", item.get("lng"))

            return Hotel(
                id=str(item["id"]),
                name=str(item.get("company") or item.get("name") or "Unknown"),
                iata_code=str(item.get("iata_code") or "").strip().upper(),
                lat=float(lat),
                lng=float(lng),
                stars=self._safe_int(item.get("stars"), default=1),
                priority=_PRIORITY_MAP.get(str(item.get("priority", "4")), "low"),
                rooms=rooms,
                prices=prices,
                amenities=amenities,
                meals=meals,
                groups=list(item.get("groups") or []),
                pet_friendly=pet_friendly,
                categories=categories,
                all_inclusive=all_inclusive,
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Failed to map hotel item %s: %s", item.get("id"), exc)
            return None

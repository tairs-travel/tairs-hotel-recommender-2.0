from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests

from app.domain.models import RoomAvailability, RoomCombination, RoomPrices

logger = logging.getLogger(__name__)

PRIORITY_MAP: dict[str, float] = {
    "1": 1.0,
    "2": 0.5,
    "3": 0.25,
    "4": 0.0,
}

# Ordered from largest to smallest — used when building room combinations
ROOM_TYPES: list[tuple[str, int]] = [
    ("quadruple", 4),
    ("triple", 3),
    ("double", 2),
    ("single", 1),
]

# Meal windows as (start_hour, end_hour) — both inclusive ends
_MEAL_WINDOWS: dict[str, tuple[int, int]] = {
    "breakfast": (6, 10),
    "lunch": (12, 15),
    "dinner": (18, 22),
}


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------

@dataclass
class RouteInfo:
    distance_km: float
    duration_seconds: float | None = None

    @property
    def duration_str(self) -> str:
        """Human-readable duration, e.g. '1h 23m' or '45m'. 'N/A' when unknown."""
        if self.duration_seconds is None:
            return "N/A"
        total_minutes = round(self.duration_seconds / 60)
        hours, minutes = divmod(total_minutes, 60)
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points in kilometres (fallback)."""
    r = 6_371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def road_distances_matrix(
    origin_lat: float,
    origin_lng: float,
    destinations: list[tuple[float, float]],
    osrm_url: str = "",
    timeout: int = 10,
) -> list[RouteInfo]:
    """Compute road distance and duration from one origin to N destinations
    in a single OSRM Table API request.

    ``destinations`` is a list of ``(lat, lng)`` tuples (one per hotel).

    Returns a ``RouteInfo`` per destination preserving the original order.
    Falls back to haversine (``duration_seconds=None``) for every destination
    when *osrm_url* is empty, the request fails, or the response is malformed.
    """
    if not destinations:
        return []

    def _fallback() -> list[RouteInfo]:
        return [
            RouteInfo(distance_km=haversine(origin_lat, origin_lng, lat, lng))
            for lat, lng in destinations
        ]

    if not osrm_url:
        return _fallback()

    # OSRM expects coordinates as (longitude, latitude).
    # Index 0 = airport (source); indices 1..N = hotels (destinations).
    coords = ";".join(
        [f"{origin_lng},{origin_lat}"]
        + [f"{lng},{lat}" for lat, lng in destinations]
    )
    url = (
        f"{osrm_url.rstrip('/')}/table/v1/driving/{coords}"
        "?sources=0&annotations=distance,duration"
    )

    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != "Ok":
            logger.warning("OSRM table API returned non-Ok code: %s", data.get("code"))
            return _fallback()

        # data["distances"][0] and data["durations"][0] are the row vectors
        # from source 0 (airport) to every coordinate, including itself at [0].
        distances = data["distances"][0][1:]  # metres  — skip self-distance at index 0
        durations = data["durations"][0][1:]  # seconds — skip self-duration at index 0

        return [
            RouteInfo(distance_km=dist / 1000.0, duration_seconds=dur)
            for dist, dur in zip(distances, durations)
        ]

    except requests.RequestException as exc:
        logger.warning("OSRM table request failed → falling back to haversine: %s", exc)
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        logger.warning("OSRM table response parsing error → falling back to haversine: %s", exc)

    return _fallback()


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def normalize(value: float, min_val: float, max_val: float) -> float:
    """Min-max normalisation to [0, 1]. Returns 0.0 when min == max."""
    if max_val == min_val:
        return 0.0
    return (value - min_val) / (max_val - min_val)


# ---------------------------------------------------------------------------
# Hotel attribute scores
# ---------------------------------------------------------------------------

def priority_score(priority: str) -> float:
    """Map a priority label to a numeric score in [0, 1]."""
    return PRIORITY_MAP.get(priority, 0.0)


def meals_score(meals: dict) -> float:
    """Fraction of meal types (breakfast, lunch, dinner) that are offered."""
    offered = sum(bool(meals.get(m)) for m in ("breakfast", "lunch", "dinner"))
    return offered / 3.0


def all_inclusive_score(is_all_inclusive: bool) -> float:
    """Binary score for all-inclusive hotels."""
    return 1.0 if is_all_inclusive else 0.0


def calculate_relevant_meals(
    check_in: datetime, check_out: datetime
) -> dict[str, float]:
    """Per-meal relevance weight across the stay.

    Returns 0.0 for all meals when check_out <= check_in.
    Each day contributes:
      1.0  — guest is present for the full meal window
      0.5  — check_in or check_out falls inside the window
      0.0  — guest is absent for the whole window
    The per-day values are summed over all days and then averaged (divided by
    the number of nights) so the result stays in [0, 1].
    """
    # Strip timezone info so naive datetime comparisons work regardless of
    # whether the caller provides offset-aware datetimes (e.g. from JS
    # toISOString() which appends a UTC 'Z' suffix).
    check_in = check_in.replace(tzinfo=None)
    check_out = check_out.replace(tzinfo=None)
    if check_out <= check_in:
        return {"breakfast": 0.0, "lunch": 0.0, "dinner": 0.0}

    nights = (check_out.date() - check_in.date()).days or 1
    totals: dict[str, float] = {"breakfast": 0.0, "lunch": 0.0, "dinner": 0.0}

    current = check_in.date()
    while current < check_out.date():
        day_start = datetime(current.year, current.month, current.day)
        for meal, (wstart, wend) in _MEAL_WINDOWS.items():
            window_open = day_start.replace(hour=wstart)
            window_close = day_start.replace(hour=wend)
            guest_start = max(check_in, day_start)
            guest_end = min(check_out, day_start + timedelta(days=1))

            if guest_end <= window_open or guest_start >= window_close:
                score = 0.0
            elif guest_start <= window_open and guest_end >= window_close:
                score = 1.0
            else:
                score = 0.5
            totals[meal] += score
        current += timedelta(days=1)

    return {meal: totals[meal] / nights for meal in totals}


def meals_time_score(meals: dict, relevance: dict) -> float:
    """Relevance-weighted meal coverage score.

    Sum of relevance weights for each offered meal, divided by the maximum
    possible (total relevance). Returns 0.0 when total relevance is zero.
    """
    meal_keys = ("breakfast", "lunch", "dinner")
    total_relevance = sum(relevance.get(m, 0.0) for m in meal_keys)
    if not total_relevance:
        return 0.0
    offered = sum(relevance.get(m, 0.0) for m in meal_keys if meals.get(m))
    return offered / total_relevance


def double_room_bonus(combo: RoomCombination, rooms: RoomAvailability) -> float:
    """Fraction of available rooms that are doubles, given the chosen combo.

    Rewards hotels where the combination relies on double rooms.
    Returns 0.0 when no rooms are available.
    """
    total_available = rooms.single + rooms.double + rooms.triple + rooms.quadruple
    if not total_available:
        return 0.0
    return combo.double / total_available


def availability_score(rooms: RoomAvailability) -> float:
    """Availability score capped at 1.0 (full score at 50+ total rooms)."""
    total = rooms.single + rooms.double + rooms.triple + rooms.quadruple
    return min(total / 50.0, 1.0)


# ---------------------------------------------------------------------------
# Score label
# ---------------------------------------------------------------------------

def get_score_label(score: float) -> str:
    if score >= 0.85:
        return "Excellent"
    if score >= 0.70:
        return "Very good"
    if score >= 0.55:
        return "Good"
    if score >= 0.40:
        return "Acceptable"
    return "Low"


# ---------------------------------------------------------------------------
# Room combination & capacity helpers
# ---------------------------------------------------------------------------

def find_min_cost_combination(
    passengers: int,
    rooms: RoomAvailability,
    prices: RoomPrices,
) -> RoomCombination | None:
    """DFS with pruning to find the minimum-cost room combination that
    accommodates exactly *passengers* guests.

    Returns None if no valid combination exists.
    """
    available = {
        "quadruple": rooms.quadruple,
        "triple":    rooms.triple,
        "double":    rooms.double,
        "single":    rooms.single,
    }
    price_map = {
        "quadruple": prices.quadruple,
        "triple":    prices.triple,
        "double":    prices.double,
        "single":    prices.single,
    }

    best: list[dict | None] = [None]  # mutable container for the closure

    def dfs(
        remaining: int,
        type_idx: int,
        current_counts: dict[str, int],
        current_cost: float,
    ) -> None:
        if remaining == 0:
            if best[0] is None or current_cost < best[0]["cost"]:
                best[0] = {"counts": dict(current_counts), "cost": current_cost}
            return

        if type_idx >= len(ROOM_TYPES):
            return  # exhausted all room types without filling all passengers

        room_type, capacity = ROOM_TYPES[type_idx]
        max_rooms = min(available[room_type], remaining // capacity)

        for count in range(max_rooms, -1, -1):
            cost_so_far = current_cost + count * price_map[room_type]
            # Prune: already at or above best known cost
            if best[0] is not None and cost_so_far >= best[0]["cost"]:
                continue
            current_counts[room_type] = count
            dfs(remaining - count * capacity, type_idx + 1, current_counts, cost_so_far)

        current_counts[room_type] = 0

    dfs(passengers, 0, {rt: 0 for rt, _ in ROOM_TYPES}, 0.0)

    if best[0] is None:
        return None

    c = best[0]["counts"]
    return RoomCombination(
        single=c["single"],
        double=c["double"],
        triple=c["triple"],
        quadruple=c["quadruple"],
        total_cost=best[0]["cost"],
    )


def estimate_capacity_range(rooms: RoomAvailability, config=None) -> dict | None:
    """Return capacity range for all-single hotels, or None for mixed inventory.

    An all-single hotel has only single rooms (double == triple == quadruple == 0).
    """
    if rooms.double != 0 or rooms.triple != 0 or rooms.quadruple != 0:
        return None
    if rooms.single == 0:
        return None

    cfg = config or _default_config()
    cr = cfg.CAPACITY_RANGE
    total = rooms.single
    return {
        "min": int(total * cr["min_factor"]),
        "max": int(total * cr["max_factor"]),
    }


def estimated_price_per_person(prices: RoomPrices, rooms: RoomAvailability) -> float:
    """Price of the room type that is actually available.

    In the estimated scenario a hotel only has single rooms, so the single
    price is used. Falls back to the first non-zero price if single is zero.
    Returns 0.0 if every price is zero.
    """
    if rooms.single > 0 and prices.single > 0:
        return prices.single
    if rooms.double > 0 and prices.double > 0:
        return prices.double
    if rooms.triple > 0 and prices.triple > 0:
        return prices.triple
    if rooms.quadruple > 0 and prices.quadruple > 0:
        return prices.quadruple
    return 0.0


def calculate_capacity_metrics(rooms: RoomAvailability, config=None) -> dict:
    """Return a dict with hard_cap, target_cap, is_estimated, and capacity_range.

    hard_cap = total_rooms * max_factor (default 2.0) for all hotel types.
    Example: 1 single + 1 double + 1 quadruple = 3 rooms * 2 = hard_cap 6.
    """
    cfg = config or _default_config()
    cr = cfg.CAPACITY_RANGE
    total_rooms = rooms.single + rooms.double + rooms.triple + rooms.quadruple
    capacity_range = estimate_capacity_range(rooms, cfg)
    is_estimated = capacity_range is not None

    hard_cap = int(total_rooms * cr["max_factor"])

    target_cap = total_rooms * cfg.OCCUPANCY_TARGET

    return {
        "hard_cap": hard_cap,
        "target_cap": target_cap,
        "is_estimated": is_estimated,
        "capacity_range": capacity_range,
    }


def saturation_ratio(assigned: int, target_cap: float) -> float:
    """Ratio of assigned passengers to target capacity.

    Returns 0.0 when target_cap <= 0.
    """
    if target_cap <= 0:
        return 0.0
    return assigned / target_cap


# ---------------------------------------------------------------------------
# Internal: lazy default config to avoid circular imports at module level
# ---------------------------------------------------------------------------

def _default_config():
    from app.config.settings import Config  # noqa: PLC0415
    return Config

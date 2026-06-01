from __future__ import annotations

import logging
import math
from itertools import combinations

from app.domain.models import Hotel, RoomAvailability, RoomCombination
from app.utils.helpers import (
    calculate_capacity_metrics,
    estimated_price_per_person,
    find_min_cost_combination,
    haversine,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MULTI_PENALTY_EXTRA_HOTEL = 0.05   # per extra hotel beyond the first
MULTI_PENALTY_SPLIT = 0.10   # per hotel split of the passenger group
MAX_CANDIDATES = 200    # max virtual candidates to generate
MAX_HOTELS_PER_GROUP = 10     # max hotels allowed in a single multi-hotel combination

# Maps priority label → sort key (lower = better)
_PRIORITY_ORDER: dict[str, int] = {
    "high": 0,
    "medium": 1,
    "medium-low": 2,
    "low": 3,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hotel_capacity(rooms: RoomAvailability) -> int:
    """Total guest capacity: single×1 + double×2 + triple×3 + quadruple×4."""
    return (
        rooms.single * 1
        + rooms.double * 2
        + rooms.triple * 3
        + rooms.quadruple * 4
    )


def _distribute_passengers_target_first(
    hotels_with_caps: list[dict],
    passengers: int,
) -> list[int] | None:
    """Distribute *passengers* across hotels in two passes.

    Each dict in *hotels_with_caps* must contain:
      - ``target_cap``: float — ideal occupancy ceiling
      - ``hard_cap``:   int   — absolute maximum guests

    Pass 1
        Each hotel is assigned ``min(floor(target_cap), hard_cap)`` passengers,
        in order.

    Pass 2
        Any remaining passengers are filled into the headroom
        (``hard_cap - assigned``) of each hotel, in order.

    Returns a list of assigned counts (same order as input), or ``None`` if
    the total hard capacity cannot cover all passengers.
    """
    n = len(hotels_with_caps)
    assigned = [0] * n

    # Pass 1 — fill up to target
    for i, h in enumerate(hotels_with_caps):
        assigned[i] = min(int(math.floor(h["target_cap"])), h["hard_cap"])

    remaining = passengers - sum(assigned)

    # Pass 2 — fill remaining into headroom
    if remaining > 0:
        for i, h in enumerate(hotels_with_caps):
            headroom = h["hard_cap"] - assigned[i]
            fill = min(headroom, remaining)
            assigned[i] += fill
            remaining -= fill
            if remaining == 0:
                break

    if remaining > 0:
        return None  # combined capacity is insufficient

    return assigned


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def build_multi_hotel_candidates(
    eligible: list[dict],
    passengers: int,
    meal_relevance: dict | None,
    has_meal_relevance: bool,
    config=None,
    primary_iata: str | None = None,
) -> list[dict]:
    """Build virtual multi-hotel candidates when no single hotel can accommodate
    all passengers.

    Parameters
    ----------
    eligible:
        List of dicts, each with keys ``hotel`` (Hotel), ``distance`` (float),
        and ``capacity`` (int).
    passengers:
        Total number of passengers to accommodate.
    meal_relevance:
        Per-meal relevance weights derived from check-in/out times, or None.
    has_meal_relevance:
        Whether *meal_relevance* was derived from actual check-in/out times.
    config:
        Config object (defaults to ``app.config.settings.Config``).

    Algorithm
    ---------
    1. Enrich each entry with capacity metrics (hard_cap, target_cap,
       is_estimated).
    2. Sort entries by hard_cap descending for early pruning.
    3. DFS over subsets of 2–3 hotels whose combined hard_cap >= passengers,
       stopping once MAX_CANDIDATES valid subsets are found.
    4. For each valid subset:
       a. Distribute passengers via ``_distribute_passengers_target_first``.
       b. For each hotel compute ``find_min_cost_combination``; if the hotel
          is all-single and returns None, fall back to
          ``estimated_price_per_person × assigned``; abort subset on any
          remaining None.
       c. Compute inter-hotel distance penalty:
          ``min(max_pairwise_haversine_km / 100, 0.05)``.
       d. Aggregate pricing, stars, priority (worst), meals/amenities (union).
       e. Return a candidate dict compatible with the single-hotel pipeline,
          enriched with ``allocations``, ``hotels_used``, ``is_estimated``,
          ``uncertainty_penalty``, ``distance_penalty``, ``extra_hotel_penalty``,
          ``split_penalty``, and ``result_type="multi"``.

    Returns
    -------
    list[dict]
        Virtual candidate dicts, or an empty list if no valid combination
        exists.
    """
    if config is None:
        from app.config.settings import Config  # noqa: PLC0415
        config = Config

    if not eligible or passengers <= 0:
        return []

    # Step 1 — enrich with capacity metrics
    enriched: list[dict] = []
    for entry in eligible:
        hotel: Hotel = entry["hotel"]
        metrics = calculate_capacity_metrics(hotel.rooms, config)
        enriched.append({
            "hotel":          hotel,
            "distance":       entry["distance"],
            "capacity":       entry["capacity"],
            "hard_cap":       metrics["hard_cap"],
            "target_cap":     metrics["target_cap"],
            "is_estimated":   metrics["is_estimated"],
            "capacity_range": metrics["capacity_range"],
        })

    # Step 2 — prefer destination IATA, then nearer hotels, then larger capacity
    primary = (primary_iata or "").upper()

    def _sort_key(entry: dict) -> tuple:
        hotel: Hotel = entry["hotel"]
        is_remote = 1 if primary and hotel.iata_code.upper() != primary else 0
        return (is_remote, entry["distance"], -entry["hard_cap"])

    enriched.sort(key=_sort_key)

    candidates: list[dict] = []

    # Step 3 — DFS over subsets of size 2–3
    def _dfs(start: int, subset: list[dict]) -> None:
        if len(candidates) >= MAX_CANDIDATES:
            return

        combined_cap = sum(e["hard_cap"] for e in subset)

        if combined_cap >= passengers and len(subset) >= 2:
            _build_candidate(subset)

        if len(subset) >= MAX_HOTELS_PER_GROUP or len(candidates) >= MAX_CANDIDATES:
            return

        for i in range(start, len(enriched)):
            _dfs(i + 1, subset + [enriched[i]])

    # Step 4 — build one candidate from a valid subset
    def _build_candidate(subset: list[dict]) -> None:
        caps = [{"target_cap": e["target_cap"], "hard_cap": e["hard_cap"]}
                for e in subset]
        assigned_counts = _distribute_passengers_target_first(caps, passengers)
        if assigned_counts is None:
            logger.debug(
                "multi_hotel_allocator: subset %s cannot cover %d passengers — skipped",
                [e["hotel"].id for e in subset],
                passengers,
            )
            return

        allocations: list[dict] = []
        total_price = 0.0
        any_estimated = False
        uncertainty_count = 0
        total_single = total_double = total_triple = total_quadruple = 0

        for entry, assigned in zip(subset, assigned_counts):
            hotel: Hotel = entry["hotel"]
            combo = find_min_cost_combination(
                assigned, hotel.rooms, hotel.prices)

            # All-single hotel with no valid combination → price estimate
            if combo is None and entry["is_estimated"]:
                ppp = estimated_price_per_person(hotel.prices, hotel.rooms)
                combo = RoomCombination(
                    single=assigned,
                    total_cost=ppp * assigned,
                )
                any_estimated = True
                uncertainty_count += 1

            if combo is None:
                # Cannot accommodate this hotel's share — abort entire subset
                return

            if entry["is_estimated"]:
                any_estimated = True

            total_price += combo.total_cost
            total_single += combo.single
            total_double += combo.double
            total_triple += combo.triple
            total_quadruple += combo.quadruple

            allocations.append({
                "hotel_id":            hotel.id,
                "hotel_name":          hotel.name,
                "stars":               hotel.stars,
                "distance_km":         entry["distance"],
                "assigned_passengers": assigned,
                "combo":               combo,
                "price":               combo.total_cost,
                "meals":               hotel.meals,
                "amenities":           hotel.amenities,
                "priority":            hotel.priority,
                "is_estimated":        entry["is_estimated"],
                "groups":              hotel.groups,
                "rooms":               {"single": hotel.rooms.single, "double": hotel.rooms.double, "triple": hotel.rooms.triple, "quadruple": hotel.rooms.quadruple},
            })

        if not allocations:
            return

        allocations.sort(key=lambda row: row["distance_km"])

        # Inter-hotel distance penalty
        coords = [(e["hotel"].lat, e["hotel"].lng) for e in subset]
        max_inter_dist = max(
            (haversine(lat1, lng1, lat2, lng2)
             for (lat1, lng1), (lat2, lng2) in combinations(coords, 2)),
            default=0.0,
        )
        distance_penalty = min(max_inter_dist / 100.0, 0.05)

        # Aggregate fields across hotels
        avg_distance = sum(e["distance"] for e in subset) / len(subset)
        avg_stars = sum(e["hotel"].stars for e in subset) / len(subset)

        worst_priority = max(
            subset,
            key=lambda e: _PRIORITY_ORDER.get(e["hotel"].priority, 3),
        )["hotel"].priority

        merged_meals:     dict = {}
        merged_amenities: dict = {}
        for entry in subset:
            merged_meals.update(entry["hotel"].meals)
            merged_amenities.update(entry["hotel"].amenities)

        composite_combo = RoomCombination(
            single=total_single,
            double=total_double,
            triple=total_triple,
            quadruple=total_quadruple,
            total_cost=total_price,
        )

        candidates.append({
            # Primary hotel kept for pipeline compatibility
            "hotel":             subset[0]["hotel"],
            "hotel_ids":         [e["hotel"].id for e in subset],
            "hotel_names":       [e["hotel"].name for e in subset],
            # Aggregated scoring fields
            "distance":          avg_distance,
            "capacity":          sum(e["hard_cap"] for e in subset),
            "stars":             avg_stars,
            "priority":          worst_priority,
            "meals":             merged_meals,
            "amenities":         merged_amenities,
            # Pricing
            "total_price":       total_price,
            "combo":             composite_combo,
            # Multi-specific
            "allocations":       allocations,
            "hotels_used":       len(subset),
            "is_estimated":      any_estimated,
            "uncertainty_penalty":    uncertainty_count * 0.02,
            "distance_penalty":       distance_penalty,
            "extra_hotel_penalty":    (len(subset) - 1) * MULTI_PENALTY_EXTRA_HOTEL,
            "split_penalty":          len(subset) * MULTI_PENALTY_SPLIT,
            "result_type":       "multi",
        })

    _dfs(0, [])

    logger.debug(
        "multi_hotel_allocator: %d candidate(s) built for %d passengers",
        len(candidates),
        passengers,
    )
    return candidates


def build_partial_multi_hotel_candidate(
    eligible: list[dict],
    passengers: int,
    config=None,
    primary_iata: str | None = None,
) -> dict | None:
    """Build a single best-effort multi-hotel candidate when full allocation
    is impossible.

    The allocator fills hotels in the same preference order used by the normal
    multi-hotel pipeline (local IATA first, then nearest overflow hotels),
    respecting each hotel's hard capacity and room-combination feasibility.
    """
    if config is None:
        from app.config.settings import Config  # noqa: PLC0415
        config = Config

    if not eligible or passengers <= 0:
        return None

    enriched: list[dict] = []
    for entry in eligible:
        hotel: Hotel = entry["hotel"]
        metrics = calculate_capacity_metrics(hotel.rooms, config)
        if metrics["hard_cap"] <= 0:
            continue
        enriched.append({
            "hotel": hotel,
            "distance": entry["distance"],
            "hard_cap": metrics["hard_cap"],
            "target_cap": metrics["target_cap"],
            "is_estimated": metrics["is_estimated"],
        })

    if not enriched:
        return None

    primary = (primary_iata or "").upper()

    def _sort_key(entry: dict) -> tuple:
        hotel: Hotel = entry["hotel"]
        is_remote = 1 if primary and hotel.iata_code.upper() != primary else 0
        return (is_remote, entry["distance"], -entry["hard_cap"])

    enriched.sort(key=_sort_key)

    def _combo_for(hotel: Hotel, assigned: int, estimated: bool) -> tuple[int, RoomCombination | None, bool]:
        for pax in range(assigned, 0, -1):
            combo = find_min_cost_combination(pax, hotel.rooms, hotel.prices)
            if combo is not None:
                return pax, combo, False
            if estimated:
                ppp = estimated_price_per_person(hotel.prices, hotel.rooms)
                if ppp > 0:
                    return pax, RoomCombination(single=pax, total_cost=ppp * pax), True
        return 0, None, False

    remaining = passengers
    allocations: list[dict] = []
    total_price = 0.0
    any_estimated = False
    uncertainty_count = 0
    total_single = total_double = total_triple = total_quadruple = 0
    used_entries: list[dict] = []

    for entry in enriched:
        if remaining <= 0:
            break

        requested_here = min(entry["hard_cap"], remaining)
        assigned, combo, used_estimation = _combo_for(
            entry["hotel"],
            requested_here,
            entry["is_estimated"],
        )
        if assigned <= 0 or combo is None:
            continue

        hotel = entry["hotel"]
        allocations.append({
            "hotel_id": hotel.id,
            "hotel_name": hotel.name,
            "stars": hotel.stars,
            "distance_km": entry["distance"],
            "assigned_passengers": assigned,
            "combo": combo,
            "price": combo.total_cost,
            "meals": hotel.meals,
            "amenities": hotel.amenities,
            "priority": hotel.priority,
            "is_estimated": entry["is_estimated"] or used_estimation,
            "groups": hotel.groups,
            "rooms": {"single": hotel.rooms.single, "double": hotel.rooms.double, "triple": hotel.rooms.triple, "quadruple": hotel.rooms.quadruple},
        })

        used_entries.append(entry)
        total_price += combo.total_cost
        total_single += combo.single
        total_double += combo.double
        total_triple += combo.triple
        total_quadruple += combo.quadruple
        remaining -= assigned

        if entry["is_estimated"] or used_estimation:
            any_estimated = True
        if used_estimation:
            uncertainty_count += 1

    if not allocations:
        return None

    allocations.sort(key=lambda row: row["distance_km"])
    assigned_total = sum(a["assigned_passengers"] for a in allocations)
    passengers_unassigned = max(passengers - assigned_total, 0)

    coords = [(e["hotel"].lat, e["hotel"].lng) for e in used_entries]
    max_inter_dist = max(
        (haversine(lat1, lng1, lat2, lng2)
         for (lat1, lng1), (lat2, lng2) in combinations(coords, 2)),
        default=0.0,
    )
    distance_penalty = min(max_inter_dist / 100.0, 0.05)

    avg_distance = sum(e["distance"] for e in used_entries) / len(used_entries)
    avg_stars = sum(e["hotel"].stars for e in used_entries) / len(used_entries)

    worst_priority = max(
        used_entries,
        key=lambda e: _PRIORITY_ORDER.get(e["hotel"].priority, 3),
    )["hotel"].priority

    merged_meals: dict = {}
    merged_amenities: dict = {}
    for entry in used_entries:
        merged_meals.update(entry["hotel"].meals)
        merged_amenities.update(entry["hotel"].amenities)

    composite_combo = RoomCombination(
        single=total_single,
        double=total_double,
        triple=total_triple,
        quadruple=total_quadruple,
        total_cost=total_price,
    )

    return {
        "hotel": used_entries[0]["hotel"],
        "hotel_ids": [e["hotel"].id for e in used_entries],
        "hotel_names": [e["hotel"].name for e in used_entries],
        "distance": avg_distance,
        "capacity": sum(e["hard_cap"] for e in used_entries),
        "stars": avg_stars,
        "priority": worst_priority,
        "meals": merged_meals,
        "amenities": merged_amenities,
        "total_price": total_price,
        "combo": composite_combo,
        "allocations": allocations,
        "hotels_used": len(used_entries),
        "is_estimated": any_estimated,
        "uncertainty_penalty": uncertainty_count * 0.02,
        "distance_penalty": distance_penalty,
        "extra_hotel_penalty": (len(used_entries) - 1) * MULTI_PENALTY_EXTRA_HOTEL,
        "split_penalty": len(used_entries) * MULTI_PENALTY_SPLIT,
        "result_type": "multi",
        "is_partial": passengers_unassigned > 0,
        "assigned_passengers_total": assigned_total,
        "passengers_unassigned": passengers_unassigned,
        "is_overflow_forced": any(
            primary and e["hotel"].iata_code.upper() != primary
            for e in used_entries
        ),
    }

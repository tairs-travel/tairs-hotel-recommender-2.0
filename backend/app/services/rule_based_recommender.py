from __future__ import annotations

import logging

from app.config.settings import Config
from app.domain.models import Hotel, RecommendationResult, RoomAvailability, RoomCombination
from app.repositories.base_repository import HotelRepository
from app.services.base_recommender import BaseRecommender
from app.services.multi_hotel_allocator import (
    build_multi_hotel_candidates,
    build_partial_multi_hotel_candidate,
)
from app.utils.airline_groups import get_expanded_groups
from app.utils.airports import get_airports_within_radius
from app.utils.helpers import (
    all_inclusive_score,
    availability_score,
    calculate_capacity_metrics,
    double_room_bonus,
    estimated_price_per_person,
    find_min_cost_combination,
    get_score_label,
    haversine,
    meals_score,
    meals_time_score,
    normalize,
    priority_score,
    road_distances_matrix,
    saturation_ratio,
)

logger = logging.getLogger(__name__)

_FACTOR_DIRECTIONS: dict[str, str] = {
    "distance": "lower",
    "priority": "higher",
    "meals": "higher",
    "meals_time": "higher",
    "all_inclusive": "higher",
}

_PRIORITY_RANK: dict[str, int] = {
    "1": 0,
    "2": 1,
    "3": 2,
    "4": 3,
}


class RuleBasedRecommender(BaseRecommender):
    def __init__(self, repository: HotelRepository, config=None) -> None:
        self._repo = repository
        self._config = config or Config
        self._last_strategy = "single-hotel"
        self._last_group_expansion: dict = {}
        self._last_warnings: list[str] = []

    @property
    def last_strategy(self) -> str:
        return self._last_strategy

    @property
    def last_group_expansion(self) -> dict:
        return dict(self._last_group_expansion)

    @property
    def last_warnings(self) -> list[str]:
        return list(self._last_warnings)

    def recommend(
        self,
        passengers: int,
        airport_lat: float,
        airport_lng: float,
        filters: dict | None = None,
        meal_relevance: dict | None = None,
        weights_override: dict | None = None,
        nearby_airport_coords: list[dict] | None = None,
        airline_group: str | None = None,
        airline_price: float | None = None,
        iata_code: str | None = None,
        pets: bool = False,
    ) -> list[RecommendationResult]:
        del nearby_airport_coords

        self._last_strategy = "single-hotel"
        self._last_group_expansion = {}
        self._last_warnings = []

        if passengers <= 0:
            return []

        requested_iata = (iata_code or "").upper() or None
        has_meal_relevance = bool(
            meal_relevance and sum(meal_relevance.values()) > 0)
        resolved_meal_relevance = meal_relevance if has_meal_relevance else None
        weights = self._resolve_weights(
            airline_price,
            resolved_meal_relevance,
            weights_override,
        )

        hotels = self._repo.get_all_hotels(iata_code=requested_iata)
        current_hotels = [
            hotel for hotel in hotels if self._hotel_matches_iata(hotel, requested_iata)
        ]

        normalized_group = self._normalize_group(airline_group)

        # ── STEP 2: collect hotel lists for each pipeline stage ───────────────
        if normalized_group and normalized_group != "UNKNOWN":
            primary_group_hotels = [
                hotel for hotel in current_hotels
                if self._hotel_matches_group(hotel, normalized_group)
            ]
            expanded_groups = get_expanded_groups(normalized_group)
            expanded_group_hotels = [
                hotel for hotel in current_hotels
                if any(self._normalize_group(g) in expanded_groups for g in hotel.groups)
            ]
        else:
            primary_group_hotels = current_hotels
            expanded_groups = []
            expanded_group_hotels = []

        # ── STEP 2b: gather hotels from all geographically nearby airports ─────
        # Replaces the old hard-coded IATA pair table: any airport within the
        # configured radius is now eligible as an overflow source, ordered by
        # ascending distance so the closest is always tried first.
        nearby_iatas: list[str] = (
            get_airports_within_radius(
                requested_iata, self._config.OVERFLOW_MAX_DISTANCE_KM)
            if requested_iata else []
        )

        overflow_secondary_hotels: list[Hotel] = []
        expanded_secondary_hotels: list[Hotel] = []
        for nb_iata in nearby_iatas:
            nb_raw = self._repo.get_all_hotels(iata_code=nb_iata)
            nb_raw = [
                hotel for hotel in nb_raw
                if self._hotel_matches_iata(hotel, nb_iata)
            ]
            if normalized_group and normalized_group != "UNKNOWN":
                nb_same = [
                    hotel for hotel in nb_raw
                    if self._hotel_matches_group(hotel, normalized_group)
                ]
                nb_exp = [
                    hotel for hotel in nb_raw
                    if any(self._normalize_group(g) in expanded_groups for g in hotel.groups)
                ]
            else:
                nb_same = nb_raw
                nb_exp = []

            nb_same = self._filter_overflow_hotels(
                nb_same, airport_lat, airport_lng)
            nb_exp = self._filter_overflow_hotels(
                nb_exp,  airport_lat, airport_lng)
            overflow_secondary_hotels.extend(nb_same)
            expanded_secondary_hotels.extend(nb_exp)

        if nearby_iatas:
            logger.debug(
                "Nearby airports within %.0f km of %s: %s",
                self._config.OVERFLOW_MAX_DISTANCE_KM,
                requested_iata,
                ", ".join(nearby_iatas),
            )

        primary_group_hotels = self._apply_filters(
            primary_group_hotels, filters)
        overflow_secondary_hotels = self._apply_filters(
            overflow_secondary_hotels, filters)
        expanded_group_hotels = self._apply_filters(
            expanded_group_hotels, filters)
        expanded_secondary_hotels = self._apply_filters(
            expanded_secondary_hotels, filters)

        capacity_scope: dict[str, Hotel] = {}
        for hotel in (
            primary_group_hotels
            + overflow_secondary_hotels
            + expanded_group_hotels
            + expanded_secondary_hotels
        ):
            capacity_scope[hotel.id] = hotel

        max_available_capacity = sum(
            self._effective_capacity(hotel)
            for hotel in capacity_scope.values()
        )

        hotel_by_id: dict[str, Hotel] = {}
        candidate_pool: list[dict] = []

        # ── STEP 3: staged pipeline ───────────────────────────────────────────
        # Stage 1–2: same IATA, primary group
        # Stage 3–4: same IATA, expanded groups
        # Stage 5–6: paired IATA, primary group (overflow)
        # Stage 7–8: paired IATA, expanded groups (overflow)
        single_primary, eligible_primary, hbi = self._build_single_candidates(
            hotels=primary_group_hotels,
            passengers=passengers,
            airport_lat=airport_lat,
            airport_lng=airport_lng,
            meal_relevance=resolved_meal_relevance,
            airline_price=airline_price,
        )
        hotel_by_id.update(hbi)

        if single_primary:
            candidate_pool = single_primary
            self._last_strategy = "single-hotel"
        elif len(eligible_primary) >= 2:
            multi = build_multi_hotel_candidates(
                eligible=eligible_primary,
                passengers=passengers,
                meal_relevance=resolved_meal_relevance,
                has_meal_relevance=has_meal_relevance,
                config=self._config,
                primary_iata=requested_iata,
            )
            if multi:
                candidate_pool = multi
                self._last_strategy = "multi-hotel"

        # Stage 3–4: group expansion — same IATA, expanded groups (e.g. A → B)
        if not candidate_pool and expanded_group_hotels:
            single_exp, eligible_exp, hbi = self._build_single_candidates(
                hotels=expanded_group_hotels,
                passengers=passengers,
                airport_lat=airport_lat,
                airport_lng=airport_lng,
                meal_relevance=resolved_meal_relevance,
                airline_price=airline_price,
            )
            hotel_by_id.update(hbi)

            if single_exp:
                candidate_pool = single_exp
                self._last_strategy = "expansion-single"
                self._last_group_expansion = {
                    "primary_group": normalized_group,
                    "expanded_groups": expanded_groups,
                    "reason": "capacity insufficient in primary group locally",
                }
                self._last_warnings.append(
                    f"Rate group expanded to {', '.join(expanded_groups)} in {requested_iata}."
                )
            elif len(eligible_exp) >= 2:
                multi_exp = build_multi_hotel_candidates(
                    eligible=eligible_exp,
                    passengers=passengers,
                    meal_relevance=resolved_meal_relevance,
                    has_meal_relevance=has_meal_relevance,
                    config=self._config,
                    primary_iata=requested_iata,
                )
                if multi_exp:
                    candidate_pool = multi_exp
                    self._last_strategy = "expansion-multi"
                    self._last_group_expansion = {
                        "primary_group": normalized_group,
                        "expanded_groups": expanded_groups,
                        "reason": "capacity insufficient in primary group locally",
                    }

        # Stage 5–6: overflow — primary group, cross-IATA
        if not candidate_pool and overflow_secondary_hotels:
            overflow_pool = primary_group_hotels + overflow_secondary_hotels
            single_overflow, eligible_overflow, hbi = self._build_single_candidates(
                hotels=overflow_pool,
                passengers=passengers,
                airport_lat=airport_lat,
                airport_lng=airport_lng,
                meal_relevance=resolved_meal_relevance,
                airline_price=airline_price,
            )
            hotel_by_id.update(hbi)

            if single_overflow:
                for c in single_overflow:
                    c["is_overflow_forced"] = True
                candidate_pool = single_overflow
                self._last_strategy = "overflow-single"
                self._last_warnings.append(
                    f"Hotels outside {requested_iata} were included due to insufficient local capacity."
                )
            elif len(eligible_overflow) >= 2:
                multi_overflow = build_multi_hotel_candidates(
                    eligible=eligible_overflow,
                    passengers=passengers,
                    meal_relevance=resolved_meal_relevance,
                    has_meal_relevance=has_meal_relevance,
                    config=self._config,
                    primary_iata=requested_iata,
                )
                if multi_overflow:
                    for c in multi_overflow:
                        c["is_overflow_forced"] = True
                    candidate_pool = multi_overflow
                    self._last_strategy = "overflow-multi"
                    self._last_warnings.append(
                        f"Hotels outside {requested_iata} were included due to insufficient local capacity."
                    )

        # Stage 7–8: group expansion + overflow — expanded groups, cross-IATA
        if not candidate_pool and expanded_secondary_hotels:
            exp_overflow_pool = expanded_group_hotels + expanded_secondary_hotels
            single_exp_ov, eligible_exp_ov, hbi = self._build_single_candidates(
                hotels=exp_overflow_pool,
                passengers=passengers,
                airport_lat=airport_lat,
                airport_lng=airport_lng,
                meal_relevance=resolved_meal_relevance,
                airline_price=airline_price,
            )
            hotel_by_id.update(hbi)

            if single_exp_ov:
                for c in single_exp_ov:
                    c["is_overflow_forced"] = True
                candidate_pool = single_exp_ov
                self._last_strategy = "expansion-overflow-single"
                self._last_group_expansion = {
                    "primary_group": normalized_group,
                    "expanded_groups": expanded_groups,
                    "reason": "capacity insufficient in expanded groups locally and in paired IATA",
                }
            elif len(eligible_exp_ov) >= 2:
                multi_exp_ov = build_multi_hotel_candidates(
                    eligible=eligible_exp_ov,
                    passengers=passengers,
                    meal_relevance=resolved_meal_relevance,
                    has_meal_relevance=has_meal_relevance,
                    config=self._config,
                    primary_iata=requested_iata,
                )
                if multi_exp_ov:
                    for c in multi_exp_ov:
                        c["is_overflow_forced"] = True
                    candidate_pool = multi_exp_ov
                    self._last_strategy = "expansion-overflow-multi"
                    self._last_group_expansion = {
                        "primary_group": normalized_group,
                        "expanded_groups": expanded_groups,
                        "reason": "capacity insufficient in expanded groups locally and in paired IATA",
                    }
                    self._last_warnings.append(
                        f"Rate group was expanded and hotels outside {requested_iata} were used."
                    )

        if not candidate_pool:
            fallback_hotels = list(capacity_scope.values())
            _, fallback_eligible, hbi = self._build_single_candidates(
                hotels=fallback_hotels,
                passengers=passengers,
                airport_lat=airport_lat,
                airport_lng=airport_lng,
                meal_relevance=resolved_meal_relevance,
                airline_price=airline_price,
            )
            hotel_by_id.update(hbi)

            partial_candidate = build_partial_multi_hotel_candidate(
                eligible=fallback_eligible,
                passengers=passengers,
                config=self._config,
                primary_iata=requested_iata,
            )
            if partial_candidate is not None:
                candidate_pool = [partial_candidate]
                self._last_strategy = "partial-overflow-multi"
                unassigned = int(partial_candidate.get(
                    "passengers_unassigned", 0))
                assigned = int(partial_candidate.get(
                    "assigned_passengers_total", 0))
                self._last_warnings.append(
                    (
                        f"Insufficient capacity for {passengers} passengers. "
                        f"{assigned} were assigned and {unassigned} remain unassigned."
                    )
                )
            else:
                self._last_strategy = "no-capacity"
                if max_available_capacity > 0:
                    self._last_warnings.append(
                        (
                            "Not enough capacity for the requested group "
                            f"({passengers} passengers). "
                            f"Maximum eligible capacity with current filters: {max_available_capacity}."
                        )
                    )
                else:
                    self._last_warnings.append(
                        "No eligible hotels with the current filters/group/IATA."
                    )
                return []

        scored_results: list[RecommendationResult] = []
        ai_weights = dict(self._config.ALL_INCLUSIVE_WEIGHTS)
        profiles = [
            self._candidate_factor_profile(
                candidate,
                hotel_by_id,
                self._candidate_scoring_passengers(candidate, passengers),
                resolved_meal_relevance,
                weights,
                ai_weights,
            )
            for candidate in candidate_pool
        ]

        factor_raws = {
            factor: [profile[factor]["raw"] for profile in profiles]
            for factor in _FACTOR_DIRECTIONS
        }

        for candidate, profile in zip(candidate_pool, profiles):
            weighted_factors: dict[str, float] = {}
            for factor, direction in _FACTOR_DIRECTIONS.items():
                factor_weight = profile[factor]["weight"]
                if factor_weight <= 0:
                    continue
                raw = profile[factor]["raw"]
                values = factor_raws[factor]
                min_val = min(values)
                max_val = max(values)
                if max_val == min_val:
                    normalized_value = 1.0
                else:
                    normalized_value = normalize(raw, min_val, max_val)
                    if direction == "lower":
                        normalized_value = 1.0 - normalized_value
                weighted_factors[factor] = factor_weight * normalized_value

            candidate_rooms = self._candidate_room_inventory(
                candidate, hotel_by_id)
            combo: RoomCombination = candidate["combo"]
            double_bonus = min(double_room_bonus(
                combo, candidate_rooms), 1.0) * 0.03
            availability_bonus = min(
                availability_score(candidate_rooms), 1.0) * 0.02

            hotels_used = max(candidate.get("hotels_used", 1), 1)
            multi_penalty = 0.0
            if hotels_used > 1:
                multi_penalty = (
                    self._config.MULTI_PENALTY_EXTRA_HOTEL * (hotels_used - 1)
                    + self._config.MULTI_PENALTY_SPLIT * (hotels_used - 1)
                    + candidate.get("distance_penalty", 0.0)
                )

            saturation_penalty = self._saturation_penalty(
                candidate, hotel_by_id)
            uncertainty_penalty = (
                self._config.UNCERTAINTY_PENALTY_PER_HOTEL
                * self._estimated_hotel_count(candidate)
            )
            remote_penalty = self._overflow_remote_penalty(
                candidate,
                hotel_by_id,
                requested_iata,
            )

            base_score = sum(weighted_factors.values()) + \
                double_bonus + availability_bonus
            operational_penalty = (
                multi_penalty + saturation_penalty
                + uncertainty_penalty + remote_penalty
            )
            capped_penalty = min(
                operational_penalty,
                base_score * self._config.PENALTY_MAX_FRACTION_OF_BASE,
            )
            final_score = max(
                0.0,
                min(1.0, base_score - capped_penalty),
            )

            score_breakdown = {
                factor: round(value, 4)
                for factor, value in weighted_factors.items()
            }
            score_breakdown["double_room_bonus"] = round(double_bonus, 4)
            score_breakdown["availability_bonus"] = round(
                availability_bonus, 4)
            score_breakdown["multi_penalty"] = round(multi_penalty, 4)
            score_breakdown["saturation_penalty"] = round(
                saturation_penalty, 4)
            score_breakdown["uncertainty_penalty"] = round(
                uncertainty_penalty, 4)
            score_breakdown["overflow_remote_penalty"] = round(
                remote_penalty, 4)
            if capped_penalty < operational_penalty:
                score_breakdown["operational_penalty_applied"] = round(
                    capped_penalty, 4)

            result = self._candidate_to_result(
                candidate=candidate,
                score=final_score,
                passengers=passengers,
                score_breakdown=score_breakdown,
                hotel_by_id=hotel_by_id,
            )
            if result.total_price > 0:
                scored_results.append(result)

        scored_results.sort(
            key=lambda item: (
                item.score,
                self._combo_priority_tiebreak(item),
                self._combo_all_inclusive_tiebreak(item),
                -item.distance_km,
            ),
            reverse=True,
        )

        if pets:
            pet_results = [r for r in scored_results if r.pet_friendly]
            non_pet_results = [r for r in scored_results if not r.pet_friendly]
            scored_results = pet_results + non_pet_results
        if self._last_strategy and "multi" in self._last_strategy:
            return scored_results[:1]
        return scored_results[: self._config.TOP_N]

    def _resolve_weights(
        self,
        airline_price: float | None,
        meal_relevance: dict | None,
        weights_override: dict | None = None,
    ) -> dict[str, float]:
        weights = dict(self._config.DEFAULT_WEIGHTS)

        # User-supplied weights override the base factors, then we normalize.
        if weights_override:
            override_values: dict[str, float] = {}
            for factor in ["distance", "priority", "meals"]:
                raw = weights_override.get(factor)
                if raw is None:
                    continue
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    override_values[factor] = value

            if override_values:
                for factor, value in override_values.items():
                    weights[factor] = value

                # Keep meals_time neutral when the user explicitly customizes UI sliders.
                if "meals_time" in weights:
                    weights["meals_time"] = 0.0

                total_weight = sum(weights.values()) or 1.0
                weights = {
                    factor: value / total_weight
                    for factor, value in weights.items()
                }
                return weights

        if meal_relevance is None or sum(meal_relevance.values()) == 0:
            weights["meals"] += weights.get("meals_time", 0.0)
            weights["meals_time"] = 0.0

        return weights

    def _build_single_candidates(
        self,
        hotels: list[Hotel],
        passengers: int,
        airport_lat: float,
        airport_lng: float,
        meal_relevance: dict | None,
        airline_price: float | None,
    ) -> tuple[list[dict], list[dict], dict[str, Hotel]]:
        del meal_relevance
        del airline_price

        hotel_by_id = {hotel.id: hotel for hotel in hotels}
        if not hotels:
            return [], [], hotel_by_id

        routes = road_distances_matrix(
            origin_lat=airport_lat,
            origin_lng=airport_lng,
            destinations=[(hotel.lat, hotel.lng) for hotel in hotels],
            osrm_url=self._config.OSRM_URL,
            timeout=self._config.OSRM_TIMEOUT,
        )

        tolerance = self._config.CAPACITY_RANGE.get("tolerance", 0.0)
        single_candidates: list[dict] = []
        eligible_any: list[dict] = []

        for hotel, route in zip(hotels, routes):
            metrics = calculate_capacity_metrics(hotel.rooms, self._config)
            real_capacity = self._room_capacity(hotel.rooms)
            combo = find_min_cost_combination(
                passengers, hotel.rooms, hotel.prices)

            if combo is not None:
                single_candidates.append({
                    "hotel": hotel,
                    "distance": route.distance_km,
                    "duration_seconds": route.duration_seconds,
                    "combo": combo,
                    "total_price": combo.total_cost,
                    "capacity": metrics["hard_cap"],
                    "target_cap": metrics["target_cap"],
                    "is_estimated": False,
                    "capacity_range": metrics["capacity_range"],
                    "result_type": "single",
                    "hotels_used": 1,
                })
            elif metrics["is_estimated"] and passengers <= metrics["hard_cap"] * (1 + tolerance):
                price_per_person = estimated_price_per_person(
                    hotel.prices, hotel.rooms)
                estimated_total = price_per_person * passengers
                if estimated_total > 0:
                    single_candidates.append({
                        "hotel": hotel,
                        "distance": route.distance_km,
                        "duration_seconds": route.duration_seconds,
                        "combo": RoomCombination(single=passengers, total_cost=estimated_total),
                        "total_price": estimated_total,
                        "capacity": metrics["hard_cap"],
                        "target_cap": metrics["target_cap"],
                        "is_estimated": True,
                        "capacity_range": metrics["capacity_range"],
                        "result_type": "single",
                        "hotels_used": 1,
                    })

            if real_capacity > 0:
                eligible_any.append({
                    "hotel": hotel,
                    "distance": route.distance_km,
                    "capacity": real_capacity,
                })

        return single_candidates, eligible_any, hotel_by_id

    def _candidate_to_result(
        self,
        candidate: dict,
        score: float,
        passengers: int,
        score_breakdown: dict,
        hotel_by_id: dict[str, Hotel],
    ) -> RecommendationResult:
        hotel = candidate["hotel"]
        hotel_ids = candidate.get("hotel_ids") or [hotel.id]
        hotel_names = candidate.get("hotel_names") or [hotel.name]
        groups = self._candidate_groups(candidate, hotel_by_id)

        hotels_coords = [
            {"name": h.name, "lat": h.lat, "lng": h.lng}
            for hid in hotel_ids
            if (h := hotel_by_id.get(hid)) is not None
        ]

        return RecommendationResult(
            hotel_id="+".join(hotel_ids),
            hotel_name=" / ".join(hotel_names),
            stars=int(round(candidate.get("stars", hotel.stars))),
            distance_km=candidate["distance"],
            room_combination=candidate["combo"],
            total_price=candidate["total_price"],
            score=score,
            priority=candidate.get("priority", hotel.priority),
            amenities=candidate.get("amenities", hotel.amenities),
            meals=candidate.get("meals", hotel.meals),
            meals_coverage=self._meal_coverage(
                candidate.get("meals", hotel.meals)),
            score_label=get_score_label(score),
            score_percentage=min(int(score * 100), 100),
            score_breakdown=score_breakdown,
            result_type=candidate.get("result_type", "single"),
            allocations=candidate.get("allocations"),
            hotels_used=candidate.get("hotels_used", 1),
            assigned_passengers=passengers if candidate.get(
                "hotels_used", 1) == 1 else None,
            passengers_unassigned=int(candidate.get(
                "passengers_unassigned", 0) or 0),
            is_estimated=candidate.get("is_estimated", False),
            is_overflow_forced=candidate.get("is_overflow_forced", False),
            capacity_range=candidate.get("capacity_range"),
            groups=groups,
            lat=hotel.lat,
            lng=hotel.lng,
            hotels_coords=hotels_coords if len(hotels_coords) > 1 else None,
            duration_seconds=candidate.get("duration_seconds"),
            pet_friendly=candidate.get("pet_friendly", hotel.pet_friendly),
            all_inclusive=candidate.get("all_inclusive", hotel.all_inclusive),
            rooms={"single": hotel.rooms.single, "double": hotel.rooms.double, "triple": hotel.rooms.triple, "quadruple": hotel.rooms.quadruple},
        )

    def _candidate_scoring_entries(
        self,
        candidate: dict,
        hotel_by_id: dict[str, Hotel],
        passengers: int,
    ) -> list[dict]:
        allocations = candidate.get("allocations")
        if allocations:
            entries: list[dict] = []
            for alloc in allocations:
                hotel = hotel_by_id.get(alloc.get("hotel_id", ""))
                if hotel is None:
                    continue
                pax = int(alloc.get("assigned_passengers") or 0)
                if pax <= 0:
                    continue
                entries.append({
                    "hotel": hotel,
                    "pax": pax,
                    "priority": alloc.get("priority", hotel.priority),
                    "meals": alloc.get("meals", hotel.meals),
                    "all_inclusive": alloc.get("all_inclusive", hotel.all_inclusive),
                })
            if entries:
                return entries

        hotel = candidate["hotel"]
        return [{
            "hotel": hotel,
            "pax": passengers,
            "priority": candidate.get("priority", hotel.priority),
            "meals": candidate.get("meals", hotel.meals),
            "all_inclusive": candidate.get("all_inclusive", hotel.all_inclusive),
        }]

    def _candidate_factor_profile(
        self,
        candidate: dict,
        hotel_by_id: dict[str, Hotel],
        passengers: int,
        meal_relevance: dict | None,
        standard_weights: dict[str, float],
        ai_weights: dict[str, float],
    ) -> dict[str, dict[str, float]]:
        entries = self._candidate_scoring_entries(
            candidate, hotel_by_id, passengers)
        total_pax = sum(entry["pax"] for entry in entries) or max(passengers, 1)

        priority_raw = 0.0
        meals_raw = 0.0
        meals_time_raw = 0.0
        all_inclusive_raw = 0.0

        priority_weight = 0.0
        meals_weight = 0.0
        meals_time_weight = 0.0
        all_inclusive_weight = 0.0

        for entry in entries:
            share = entry["pax"] / total_pax
            if entry["all_inclusive"]:
                priority_raw += share * priority_score(entry["priority"])
                priority_weight += share * ai_weights["priority"]
                all_inclusive_raw += share * all_inclusive_score(True)
                all_inclusive_weight += share * ai_weights["all_inclusive"]
            else:
                priority_raw += share * priority_score(entry["priority"])
                priority_weight += share * standard_weights["priority"]
                meals_raw += share * meals_score(entry["meals"])
                meals_weight += share * standard_weights.get("meals", 0.0)
                if standard_weights.get("meals_time", 0.0) > 0 and meal_relevance:
                    meals_time_raw += share * meals_time_score(
                        entry["meals"], meal_relevance
                    )
                    meals_time_weight += share * standard_weights["meals_time"]

        distance_weight = standard_weights.get(
            "distance", ai_weights.get("distance", 0.15))

        return {
            "distance": {"raw": candidate["distance"], "weight": distance_weight},
            "priority": {"raw": priority_raw, "weight": priority_weight},
            "meals": {"raw": meals_raw, "weight": meals_weight},
            "meals_time": {"raw": meals_time_raw, "weight": meals_time_weight},
            "all_inclusive": {
                "raw": all_inclusive_raw,
                "weight": all_inclusive_weight,
            },
        }

    def _saturation_penalty(self, candidate: dict, hotel_by_id: dict[str, Hotel]) -> float:
        allocations = candidate.get("allocations")
        if not allocations:
            target_cap = candidate.get("target_cap", 0.0)
            if target_cap <= 0:
                return 0.0
            ratio = saturation_ratio(candidate["combo"].single + candidate["combo"].double *
                                     2 + candidate["combo"].triple * 3 + candidate["combo"].quadruple * 4, target_cap)
            return max(0.0, ratio - 1.0) * self._config.SATURATION_PENALTY_WEIGHT

        total = 0.0
        for allocation in allocations:
            hotel = hotel_by_id.get(allocation["hotel_id"])
            if hotel is None:
                continue
            target_cap = calculate_capacity_metrics(
                hotel.rooms, self._config)["target_cap"]
            ratio = saturation_ratio(
                allocation["assigned_passengers"], target_cap)
            if ratio > 1.0:
                total += (ratio - 1.0) * self._config.SATURATION_PENALTY_WEIGHT
        return total

    def _candidate_scoring_passengers(self, candidate: dict, requested_passengers: int) -> int:
        assigned = int(candidate.get("assigned_passengers_total", 0) or 0)
        if assigned > 0:
            return assigned
        return max(requested_passengers, 1)

    def _estimated_hotel_count(self, candidate: dict) -> int:
        allocations = candidate.get("allocations")
        if allocations:
            return sum(1 for allocation in allocations if allocation.get("is_estimated"))
        return 1 if candidate.get("is_estimated") else 0

    @staticmethod
    def _combo_priority_tiebreak(result: RecommendationResult) -> tuple[int, int, int, int]:
        counts = {"1": 0, "2": 0, "3": 0, "4": 0}
        allocations = result.allocations
        if allocations:
            for alloc in allocations:
                priority = str(alloc.get("priority", "4"))
                if priority in counts:
                    counts[priority] += 1
                else:
                    counts["4"] += 1
        else:
            priority = str(result.priority)
            key = priority if priority in counts else "4"
            counts[key] = 1
        return (
            counts["1"],
            counts["2"],
            counts["3"],
            -counts["4"],
        )

    @staticmethod
    def _combo_all_inclusive_tiebreak(result: RecommendationResult) -> int:
        allocations = result.allocations
        if allocations:
            return sum(
                1 for alloc in allocations if alloc.get("all_inclusive")
            )
        return 1 if result.all_inclusive else 0

    def _candidate_room_inventory(
        self,
        candidate: dict,
        hotel_by_id: dict[str, Hotel],
    ) -> RoomAvailability:
        allocations = candidate.get("allocations")
        if not allocations:
            return candidate["hotel"].rooms

        rooms = RoomAvailability()
        for allocation in allocations:
            hotel = hotel_by_id.get(allocation["hotel_id"])
            if hotel is None:
                continue
            rooms.single += hotel.rooms.single
            rooms.double += hotel.rooms.double
            rooms.triple += hotel.rooms.triple
            rooms.quadruple += hotel.rooms.quadruple
        return rooms

    def _candidate_groups(self, candidate: dict, hotel_by_id: dict[str, Hotel]) -> list:
        allocations = candidate.get("allocations")
        if not allocations:
            return list(candidate["hotel"].groups)

        seen: set[str] = set()
        groups: list[str] = []
        for allocation in allocations:
            hotel = hotel_by_id.get(allocation["hotel_id"])
            source_groups = hotel.groups if hotel is not None else []
            for group in source_groups:
                if group not in seen:
                    seen.add(group)
                    groups.append(group)
        return groups

    def _meal_coverage(self, meals: dict) -> dict[str, bool]:
        return {
            "breakfast": bool(meals.get("breakfast")),
            "lunch": bool(meals.get("lunch")),
            "dinner": bool(meals.get("dinner")),
        }

    def _effective_capacity(self, hotel: Hotel) -> int:
        return calculate_capacity_metrics(hotel.rooms, self._config)["hard_cap"]

    def _room_capacity(self, rooms: RoomAvailability) -> int:
        return (
            rooms.single
            + rooms.double * 2
            + rooms.triple * 3
            + rooms.quadruple * 4
        )

    def _filter_overflow_hotels(
        self,
        hotels: list[Hotel],
        airport_lat: float,
        airport_lng: float,
    ) -> list[Hotel]:
        max_km = self._config.OVERFLOW_MAX_DISTANCE_KM
        return [
            hotel
            for hotel in hotels
            if haversine(airport_lat, airport_lng, hotel.lat, hotel.lng) <= max_km
        ]

    def _overflow_remote_penalty(
        self,
        candidate: dict,
        hotel_by_id: dict[str, Hotel],
        requested_iata: str | None,
    ) -> float:
        if not candidate.get("is_overflow_forced") or not requested_iata:
            return 0.0

        penalty_rate = self._config.OVERFLOW_REMOTE_KM_PENALTY
        primary = requested_iata.upper()
        total = 0.0

        allocations = candidate.get("allocations") or []
        if allocations:
            for alloc in allocations:
                hotel = hotel_by_id.get(alloc["hotel_id"])
                if hotel is None or hotel.iata_code.upper() == primary:
                    continue
                total += penalty_rate * float(alloc.get("distance_km") or 0.0)
        else:
            hotel = candidate.get("hotel")
            if hotel is not None and hotel.iata_code.upper() != primary:
                total += penalty_rate * float(candidate.get("distance", 0.0))

        return min(total, 0.35)

    def _hotel_matches_group(self, hotel: Hotel, target_group: str) -> bool:
        return any(self._normalize_group(group) == target_group for group in hotel.groups)

    def _hotel_matches_iata(self, hotel: Hotel, requested_iata: str | None) -> bool:
        if requested_iata is None:
            return True
        hotel_iata = (hotel.iata_code or requested_iata).upper()
        return hotel_iata == requested_iata

    def _normalize_group(self, group: str | None) -> str | None:
        if group is None:
            return None
        return str(group).strip().upper() or None

    def _apply_filters(self, hotels: list[Hotel], filters: dict | None) -> list[Hotel]:
        if not filters:
            return hotels

        allowed_priorities = {
            self._normalize_group(priority)
            for priority in filters.get("priorities", [])
        }
        required_groups = {
            self._normalize_group(group)
            for group in filters.get("groups", [])
        }
        hotel_ids = {str(hotel_id)
                     for hotel_id in filters.get("hotel_ids", [])}

        filtered: list[Hotel] = []
        for hotel in hotels:
            if "min_stars" in filters and hotel.stars < filters["min_stars"]:
                continue
            if "max_stars" in filters and hotel.stars > filters["max_stars"]:
                continue
            if hotel_ids and hotel.id not in hotel_ids:
                continue
            if allowed_priorities and self._normalize_group(hotel.priority) not in allowed_priorities:
                continue
            if required_groups and not any(self._normalize_group(group) in required_groups for group in hotel.groups):
                continue
            filtered.append(hotel)
        return filtered

"""Controller for recommendation requests — validates input, delegates to service."""

from __future__ import annotations

import logging
from datetime import datetime

from flask import Request, jsonify
from werkzeug.exceptions import BadRequest

from app.repositories.airline_repository import APIAirlineRepository
from app.services import get_recommender
from app.utils.airline_groups import get_airline_group
from app.utils.airports import get_airport
from app.utils.helpers import calculate_relevant_meals

logger = logging.getLogger(__name__)

_airline_repo = APIAirlineRepository()


def handle_recommendations(request: Request):
    """Validate the incoming JSON payload and return scored recommendations."""
    body = request.get_json(silent=True)
    if body is None:
        raise BadRequest("Request body must be valid JSON")

    passengers = body.get("passengers")
    if not isinstance(passengers, int) or passengers < 1:
        raise BadRequest("'passengers' must be a positive integer")

    airline_display_name = body.get("airline")
    if not airline_display_name:
        raise BadRequest(
            "'airline' is required (use the display_name from /airlines)")

    airline = _airline_repo.get_by_display_name(str(airline_display_name))
    if airline is None:
        raise BadRequest(
            f"Unknown airline: '{airline_display_name}'. Check /airlines for valid options."
        )

    if airline.coach_price <= 0:
        raise BadRequest(
            f"Airline '{airline_display_name}' has no valid tourist price.")

    destination_display = body.get("destination")
    if destination_display and destination_display != airline_display_name:
        dest_airline = _airline_repo.get_by_display_name(
            str(destination_display))
        if dest_airline is None:
            raise BadRequest(
                f"Unknown destination: '{destination_display}'. Check /airlines for valid options."
            )
        dest_iata = dest_airline.iata_code
    else:
        dest_iata = airline.iata_code
        destination_display = airline_display_name

    airport_info = get_airport(dest_iata)
    if airport_info is None:
        raise BadRequest(f"No airport data found for IATA code '{dest_iata}'")

    lat = float(airport_info["lat"])
    lng = float(airport_info["lng"])
    airport_name = airport_info.get("name") or f"{dest_iata.upper()} Airport"

    airline_group = get_airline_group(airline.coach_price)

    raw_weights = body.get("weights")
    weights_override = _parse_weights(
        raw_weights) if isinstance(raw_weights, dict) else None

    filters = None
    raw_filters = body.get("filters")
    if isinstance(raw_filters, dict):
        filters = {}
        max_price = raw_filters.get("max_price")
        if max_price is not None:
            try:
                max_price = float(max_price)
                if max_price > 0:
                    filters["max_price"] = max_price
            except (TypeError, ValueError):
                logger.warning("Invalid max_price value, ignoring")
        if not filters:
            filters = None

    meal_relevance = None
    raw_check_in = body.get("check_in")
    raw_check_out = body.get("check_out")
    if raw_check_in and raw_check_out:
        try:
            check_in = datetime.fromisoformat(
                str(raw_check_in)).replace(tzinfo=None)
            check_out = datetime.fromisoformat(
                str(raw_check_out)).replace(tzinfo=None)
        except (ValueError, TypeError) as exc:
            raise BadRequest(
                "'check_in' and 'check_out' must be valid ISO datetime strings"
            ) from exc
        if check_out <= check_in:
            raise BadRequest("'check_out' must be after 'check_in'")
        meal_relevance = calculate_relevant_meals(check_in, check_out)

    recommender = get_recommender()
    pets = bool(body.get("pets", False))
    results = recommender.recommend(
        passengers,
        lat,
        lng,
        filters=filters,
        meal_relevance=meal_relevance,
        weights_override=weights_override,
        airline_group=airline_group,
        airline_price=airline.coach_price,
        iata_code=dest_iata,
        pets=pets,
    )

    if filters and filters.get("max_price") is not None:
        max_p = filters["max_price"]
        results = [r for r in results if r.total_price <= max_p]

    strategy = recommender.last_strategy
    group_expansion = recommender.last_group_expansion

    response = {
        "passengers": passengers,
        "airline": airline.display_name,
        "destination": destination_display,
        "airline_group": airline_group,
        "airline_class": body.get("airline_class"),
        "airport": {"lat": lat, "lng": lng},
        "airport_code": dest_iata.upper(),
        "airport_name": airport_name,
        "strategy": strategy,
        "results_count": len(results),
        "recommendations": [r.to_dict() for r in results],
    }
    warnings = recommender.last_warnings
    if warnings:
        response["warnings"] = warnings
    if group_expansion:
        response["group_expansion"] = True
        response["expanded_groups"] = group_expansion.get(
            "expanded_groups", [])
    return jsonify(response)


def _parse_weights(raw_weights: dict) -> dict[str, float] | None:
    try:
        w_distance = float(raw_weights.get("distance", 0))
        w_priority = float(raw_weights.get("priority", 0))
        w_meals = float(raw_weights.get("meals", 0))
        main_values = [w_distance, w_priority]
        if all(1 <= v <= 5 for v in main_values):
            if w_meals < 1:
                w_meals = 1
            total = w_distance + w_priority + w_meals
            return {
                "distance": w_distance / total,
                "priority": w_priority / total,
                "meals": w_meals / total,
            }
    except (TypeError, ValueError):
        return None
    return None

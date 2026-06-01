from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import RecommendationResult


class BaseRecommender(ABC):
    """Abstract base class for hotel recommendation strategies.

    Implementations
    ---------------
    - RuleBasedRecommender (current):
        Scores hotels using a weighted combination of distance, stars, price,
        priority, meals, and price alignment against the airline fare.
    - MLRecommender (future):
        Ranks hotels via a trained model (e.g. gradient boosting / neural net)
        that learns weights from historical booking data.
    - HybridRecommender (future):
        Combines rule-based scoring with ML re-ranking for cold-start
        resilience and adaptability.
    """

    @abstractmethod
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
        """Return an ordered list of hotel recommendations.

        Parameters
        ----------
        passengers:
            Total number of passengers to accommodate.
        airport_lat / airport_lng:
            Coordinates of the origin airport used to compute distances.
        filters:
            Optional key/value pairs to pre-filter the hotel catalogue
            (e.g. ``{"min_stars": 3}``).
        meal_relevance:
            Per-meal relevance weights derived from check-in/check-out times
            (``{"breakfast": 1.0, "lunch": 0.5, "dinner": 0.0}``).
            When *None*, equal weights are assumed.
        nearby_airport_coords:
            Additional airport coordinates (dicts with ``lat``/``lng``) used
            to expand the candidate hotel pool.
        airline_group:
            Airline group identifier (e.g. ``"A"``) used to filter hotels
            that serve a specific carrier group.
        airline_price:
            Economy-class price per passenger for the flight, used to align
            hotel cost recommendations with the fare.
        iata_code:
            IATA code of the destination airport, used to scope the hotel
            catalogue fetch.

        Returns
        -------
        list[RecommendationResult]
            Hotels ranked from best to worst match, capped at ``TOP_N``.
        """

    @property
    def last_strategy(self) -> str:
        return ""

    @property
    def last_group_expansion(self) -> dict:
        return {}

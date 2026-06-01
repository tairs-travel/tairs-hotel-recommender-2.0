from __future__ import annotations

from app.repositories.api_repository import APIHotelRepository
from app.services.base_recommender import BaseRecommender
from app.services.rule_based_recommender import RuleBasedRecommender

# Singleton repository — preserves the in-memory TTL cache across requests.
_hotel_repo: APIHotelRepository | None = None


def get_recommender(config=None) -> BaseRecommender:
    """Return a recommender backed by the shared hotel repository singleton.

    The repository is initialised once on the first call so that its
    in-memory TTL cache survives across HTTP requests.

    To use MLRecommender: replace RuleBasedRecommender with MLRecommender here.
    """
    global _hotel_repo
    if _hotel_repo is None:
        _hotel_repo = APIHotelRepository(config=config)
    return RuleBasedRecommender(repository=_hotel_repo, config=config)

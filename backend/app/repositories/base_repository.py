from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import Hotel


class HotelRepository(ABC):

    @abstractmethod
    def get_all_hotels(self, iata_code: str | None = None) -> list[Hotel]:
        """Return all hotels, optionally filtered by destination IATA code."""

    @abstractmethod
    def get_hotel_by_id(self, hotel_id: str) -> Hotel | None:
        """Return a single hotel by its ID, or None if not found."""

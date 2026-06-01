from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Airline:
    name: str           # Airline company name, e.g. "Arajet"
    iata_code: str      # Destination airport IATA code, e.g. "PUJ"
    coach_price: float  # Economy class price per passenger
    country: str = ""   # Country of the destination airport
    lat: float = 0.0    # Airport latitude
    lng: float = 0.0    # Airport longitude

    @property
    def display_name(self) -> str:
        """Returns a human-readable label, e.g. 'Arajet (PUJ)'."""
        return f"{self.name} ({self.iata_code})"

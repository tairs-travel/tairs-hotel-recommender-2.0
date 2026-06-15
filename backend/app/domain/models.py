from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RoomAvailability:
    single: int = 0
    double: int = 0
    triple: int = 0
    quadruple: int = 0


@dataclass
class RoomPrices:
    single: float = 0.0
    double: float = 0.0
    triple: float = 0.0
    quadruple: float = 0.0


@dataclass
class Hotel:
    id: str
    name: str
    lat: float
    lng: float
    stars: int  # 1–5
    priority: str  # "high" | "medium" | "medium-low" | "low"
    iata_code: str = ""
    rooms: RoomAvailability = field(default_factory=RoomAvailability)
    prices: RoomPrices = field(default_factory=RoomPrices)
    amenities: dict = field(default_factory=dict)
    meals: dict = field(default_factory=dict)  # breakfast/lunch/dinner → bool
    groups: list = field(default_factory=list)  # e.g. ["A", "B"]
    pet_friendly: bool = False
    categories: list = field(default_factory=list)
    all_inclusive: bool = False


@dataclass
class RoomCombination:
    single: int = 0
    double: int = 0
    triple: int = 0
    quadruple: int = 0
    total_cost: float = 0.0


@dataclass
class RecommendationResult:
    hotel_id: str
    hotel_name: str
    stars: int
    distance_km: float
    room_combination: RoomCombination
    total_price: float
    score: float
    priority: str = "medium"
    amenities: dict = field(default_factory=dict)
    meals: dict = field(default_factory=dict)
    meals_coverage: dict | None = None
    score_label: str = ""
    score_percentage: int = 0
    score_breakdown: dict = field(default_factory=dict)
    result_type: str = "single"
    allocations: list | None = None
    hotels_used: int = 1
    assigned_passengers: int | None = None
    passengers_unassigned: int = 0
    is_estimated: bool = False
    is_overflow_forced: bool = False
    capacity_range: dict | None = None
    groups: list = field(default_factory=list)
    lat: float = 0.0
    lng: float = 0.0
    hotels_coords: list | None = None  # [{name, lat, lng}] for multi-hotel
    duration_seconds: float | None = None
    pet_friendly: bool = False
    all_inclusive: bool = False
    rooms: dict = field(default_factory=dict)  # {single, double, triple, quadruple}

    def _serialize_allocations(self) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for alloc in (self.allocations or []):
            combo = alloc.get("combo")
            if hasattr(combo, "single"):
                room_combination = {
                    "single": combo.single,
                    "double": combo.double,
                    "triple": combo.triple,
                    "quadruple": combo.quadruple,
                }
            else:
                room_combination = combo or {}
            serialized.append({
                "hotel_id": alloc.get("hotel_id"),
                "hotel_name": alloc.get("hotel_name"),
                "stars": alloc.get("stars"),
                "distance_km": alloc.get("distance_km"),
                "assigned_passengers": alloc.get("assigned_passengers"),
                "room_combination": room_combination,
                "total_price": round(float(alloc.get("price", 0)), 2),
                "meals": alloc.get("meals", {}),
                "all_inclusive": alloc.get("all_inclusive", False),
                "amenities": alloc.get("amenities", {}),
                "priority": alloc.get("priority"),
                "is_estimated": alloc.get("is_estimated", False),
                "groups": alloc.get("groups", []),
                "rooms": alloc.get("rooms", {}),
            })
        return serialized

    def to_dict(self) -> dict[str, Any]:
        if self.result_type == "multi":
            data: dict[str, Any] = {
                "type": "multi",
                "hotels_used": self.hotels_used,
                "allocations": self._serialize_allocations(),
                "hotels_coords": self.hotels_coords or [],
            }
            if self.pet_friendly:
                data["pet_friendly"] = True
            if self.is_overflow_forced:
                data["is_overflow_forced"] = True
            if self.passengers_unassigned > 0:
                data["passengers_unassigned"] = self.passengers_unassigned
            return data

        rc = self.room_combination
        data: dict[str, Any] = {
            "type": self.result_type,
            "hotel_id": self.hotel_id,
            "hotel_name": self.hotel_name,
            "stars": self.stars,
            "distance_km": self.distance_km,
            "room_combination": {
                "single": rc.single,
                "double": rc.double,
                "triple": rc.triple,
                "quadruple": rc.quadruple,
            },
            "total_price": round(self.total_price, 2),
            "score": round(self.score, 4),
            "score_label": self.score_label,
            "score_percentage": self.score_percentage,
            "score_breakdown": self.score_breakdown,
            "amenities": self.amenities,
            "meals": self.meals,
            "all_inclusive": self.all_inclusive,
            "groups": self.groups,
            "lat": self.lat,
            "lng": self.lng,
            "pet_friendly": self.pet_friendly,
            "priority": self.priority,
            "rooms": self.rooms,
        }

        if self.duration_seconds is not None:
            data["duration_seconds"] = self.duration_seconds

        if self.assigned_passengers is not None:
            data["assigned_passengers"] = self.assigned_passengers

        if self.passengers_unassigned > 0:
            data["passengers_unassigned"] = self.passengers_unassigned

        if self.meals_coverage is not None:
            data["meals_coverage"] = self.meals_coverage

        if self.is_estimated:
            data["is_estimated"] = True
            if self.capacity_range is not None:
                data["capacity_range"] = self.capacity_range

        if self.is_overflow_forced:
            data["is_overflow_forced"] = True

        return data

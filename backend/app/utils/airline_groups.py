from __future__ import annotations

# Price boundaries are inclusive on both ends: low <= price <= high
_GROUPS: list[tuple[str, float, float]] = [
    ("A", 120.00, 139.99),
    ("B", 140.00, 159.99),
    ("C", 160.00, 200.00),
]

_EXPANSION: dict[str, list[str]] = {
    "A": ["A", "B"],
    "B": ["A", "B", "C"],
    "C": ["B", "C"],
}


def get_airline_group(price: float) -> str:
    """Return the price group letter for *price*, or 'UNKNOWN' if invalid."""
    if price <= 0:
        return "UNKNOWN"
    for group, low, high in _GROUPS:
        if low <= price <= high:
            return group
    lowest_group, lowest_low, _ = _GROUPS[0]
    highest_group, _, highest_high = _GROUPS[-1]
    if price < lowest_low:
        return lowest_group
    if price > highest_high:
        return highest_group
    return "UNKNOWN"


def get_expanded_groups(group: str) -> list[str]:
    """Return the expansion set for *group*, sorted alphabetically.

    Falls back to [group] for unrecognised values (e.g. 'UNKNOWN').
    """
    return sorted(_EXPANSION.get(group, [group]))

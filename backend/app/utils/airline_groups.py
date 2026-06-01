from __future__ import annotations

# Price boundaries are inclusive on both ends: low <= price <= high
_GROUPS: list[tuple[str, float, float]] = [
    ("A", 140.00, 149.99),
    ("B", 150.00, 159.99),
    ("C", 160.00, 169.99),
    ("D", 170.00, 185.00),
]

_EXPANSION: dict[str, list[str]] = {
    "A": ["A", "B"],
    "B": ["A", "B", "C"],
    "C": ["B", "C", "D"],
    "D": ["C", "D"],
}


def get_airline_group(price: float) -> str:
    """Return the price group letter for *price*, or 'UNKNOWN' if out of range."""
    for group, low, high in _GROUPS:
        if low <= price <= high:
            return group
    return "UNKNOWN"


def get_expanded_groups(group: str) -> list[str]:
    """Return the expansion set for *group*, sorted alphabetically.

    Falls back to [group] for unrecognised values (e.g. 'UNKNOWN').
    """
    return sorted(_EXPANSION.get(group, [group]))

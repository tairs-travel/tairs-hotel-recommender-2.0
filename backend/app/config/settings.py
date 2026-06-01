import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    # ------------------------------------------------------------------ server
    DEBUG: bool = os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")
    HOST: str = os.getenv("FLASK_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("FLASK_PORT", 5000))

    # ----------------------------------------------------------------- logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    # ------------------------------------------------------------ external APIs
    HOTELS_API_URL: str = os.getenv(
        "HOTELS_API_URL", "http://84.247.185.239:5004/api/hotels/export"
    )
    HOTELS_API_KEY: str = os.getenv("HOTELS_API_KEY", "")

    AIRLINES_API_URL: str = os.getenv("AIRLINES_API_URL", "")
    AIRLINES_API_KEY: str = os.getenv("AIRLINES_API_KEY", "")

    # -------------------------------------------------------------------- OSRM
    OSRM_URL: str = os.getenv("OSRM_URL", "http://localhost:5003")
    OSRM_TIMEOUT: int = int(os.getenv("OSRM_TIMEOUT", 3))

    # ------------------------------------------------------- cache & resilience
    CACHE_BACKEND: str = os.getenv("CACHE_BACKEND", "memory")  # memory | none
    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", 60))
    CACHE_STALE_TTL_SECONDS: int = int(os.getenv("CACHE_STALE_TTL_SECONDS", 300))
    POLL_INTERVAL_SECONDS: int = int(os.getenv("POLL_INTERVAL_SECONDS", 50))
    CIRCUIT_BREAKER_FAILURES: int = int(os.getenv("CIRCUIT_BREAKER_FAILURES", 3))
    CIRCUIT_BREAKER_TIMEOUT: int = int(os.getenv("CIRCUIT_BREAKER_TIMEOUT", 30))

    # --------------------------------------------------------- algorithm tuning
    TOP_N: int = int(os.getenv("TOP_N", 10))

    # Max haversine distance (km) from the destination airport used to discover
    # nearby overflow airports and filter their hotels.  250 km matches the
    # geographic rule stated in the allocation algorithm (e.g. PUJ → SDQ).
    OVERFLOW_MAX_DISTANCE_KM: float = float(os.getenv("OVERFLOW_MAX_DISTANCE_KM", "250"))

    # Extra score penalty per km for each hotel outside the destination IATA in overflow plans.
    OVERFLOW_REMOTE_KM_PENALTY: float = float(os.getenv("OVERFLOW_REMOTE_KM_PENALTY", "0.0004"))

    # Capacity bounds relative to the requested group size.
    # A hotel is considered when:
    #   capacity ∈ [group_size * min_factor - tolerance,
    #                group_size * max_factor + tolerance]
    CAPACITY_RANGE: dict = {
        "min_factor": 1.0,
        "max_factor": 2.0,
        "tolerance": 0.1,
    }

    # Ideal occupancy ratio (booked / capacity) used to score hotels.
    OCCUPANCY_TARGET: float = 1.6

    # Penalty multiplier applied when a hotel's occupancy exceeds the target.
    SATURATION_PENALTY_WEIGHT: float = 0.05

    # Base scoring weights — must sum to 1.0.
    #
    # Redistribution rules (applied at scoring time, not stored here):
    #   • When no meal dates are provided, meals_time weight is added to meals.
    DEFAULT_WEIGHTS: dict = {
        "distance":   0.15,
        "priority":   0.75,
        "meals":      0.05,
        "meals_time": 0.05,
    }


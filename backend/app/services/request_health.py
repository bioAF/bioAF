"""In-memory request health tracking.

The logging middleware calls record() on every response. The health
endpoint reads the counters to derive per-service health status over
a rolling 5-minute window.

Services are mapped from API route prefixes. A service with >75% success
rate is healthy, 50-74% is degraded, <50% is unhealthy. Services with
no traffic in the window are unknown.
"""

import time

WINDOW_SECONDS = 300  # 5 minutes

# Map route prefixes to service names. Order matters -- first match wins.
_ROUTE_SERVICE_MAP: list[tuple[str, str]] = [
    ("/api/experiments", "experiments"),
    ("/api/samples", "samples"),
    ("/api/projects", "projects"),
    ("/api/pipelines", "pipelines"),
    ("/api/pipeline-runs", "pipelines"),
    ("/api/v1/notebooks", "notebooks"),
    ("/api/files", "storage"),
    ("/api/v1/environments", "environments"),
    ("/api/backups", "backups"),
    ("/api/auth", "auth"),
    ("/api/users", "auth"),
    ("/api/notifications", "notifications"),
    ("/api/v1/infrastructure", "infrastructure"),
    ("/api/components", "infrastructure"),
]


def _classify_route(path: str) -> str | None:
    """Map a request path to a service name."""
    for prefix, service in _ROUTE_SERVICE_MAP:
        if path.startswith(prefix):
            return service
    return None


# Each entry: list of (timestamp, is_success) tuples
_counters: dict[str, list[tuple[float, bool]]] = {}


def record(path: str, status_code: int) -> None:
    """Record a request outcome. Called by the logging middleware."""
    service = _classify_route(path)
    if service is None:
        return
    now = time.time()
    is_success = status_code < 400
    if service not in _counters:
        _counters[service] = []
    _counters[service].append((now, is_success))


def get_service_health() -> dict[str, str]:
    """Compute health status for each service based on the rolling window.

    Returns a dict of {service_name: "healthy"|"degraded"|"unhealthy"|"unknown"}.
    """
    now = time.time()
    cutoff = now - WINDOW_SECONDS
    result: dict[str, str] = {}

    for service, entries in _counters.items():
        # Prune old entries
        recent = [(ts, ok) for ts, ok in entries if ts >= cutoff]
        _counters[service] = recent

        if not recent:
            result[service] = "unknown"
            continue

        total = len(recent)
        successes = sum(1 for _, ok in recent if ok)
        rate = successes / total

        if rate >= 0.75:
            result[service] = "healthy"
        elif rate >= 0.50:
            result[service] = "degraded"
        else:
            result[service] = "unhealthy"

    return result


def clear() -> None:
    """Clear all counters. Used by tests."""
    _counters.clear()

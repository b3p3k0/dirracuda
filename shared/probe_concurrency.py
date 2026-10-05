"""Shared worker policy for batch probes, independent of GUI imports."""

PROBE_WORKERS_DEFAULT = 12
PROBE_WORKERS_MAX = 16
PROBE_WORKERS_MIN = 1


def clamp_probe_workers(value) -> int:
    """Coerce a worker count to an integer and clamp it to the probe range."""
    if value is None or isinstance(value, bool):
        return PROBE_WORKERS_DEFAULT
    try:
        workers = int(value)
    except (TypeError, ValueError, OverflowError):
        return PROBE_WORKERS_DEFAULT
    return max(PROBE_WORKERS_MIN, min(PROBE_WORKERS_MAX, workers))


def resolve_probe_workers(settings_manager) -> int:
    """Honor saved probe workers, falling back if settings cannot be read."""
    try:
        return clamp_probe_workers(settings_manager.get_setting(
            "probe.batch_max_workers", PROBE_WORKERS_DEFAULT
        ))
    except Exception:
        return PROBE_WORKERS_DEFAULT

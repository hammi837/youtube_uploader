"""
scheduler.py — Timezone-aware datetime parsing and validation for YouTube scheduling.

Design:
  - Bare datetime strings (no offset) are treated as UTC, NOT local server time.
    The FastAPI frontend will always send explicit UTC offsets; bare strings in
    the CLI are documented as UTC-assumed to avoid DST ambiguity on the server.
  - Explicit offsets (e.g. +05:00, +02:00, Z) are parsed and converted to UTC.
  - IANA timezone names (e.g. "Asia/Karachi") are supported when passed alongside
    a bare datetime via parse_schedule_time_with_zone().
  - All returned datetimes are UTC-aware.
  - Uses Python stdlib zoneinfo (Python 3.9+) for DST-correct conversions.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Optional


# Minimum lead time before the scheduled publish time
MIN_LEAD_SECONDS = 120   # 2 minutes (YouTube itself requires ~1 min; we add buffer)


# ── Public API ─────────────────────────────────────────────────────────────────

def parse_schedule_time(schedule_str: str) -> datetime:
    """
    Parse a schedule string and return a timezone-aware UTC datetime.

    Accepted formats
    ----------------
    - "2026-09-05T18:00:00Z"          → UTC explicit
    - "2026-09-05T18:00:00+00:00"     → UTC explicit
    - "2026-09-05T13:00:00+05:00"     → UTC+5 (e.g. Pakistan/PKT), no DST issue
    - "2026-09-05T18:00:00+02:00"     → UTC+2 (e.g. CEST during European summer)
    - "2026-09-05T18:00:00"           → Assumed UTC (bare string, no offset)

    Important: bare datetime strings without an offset are treated as UTC.
    For local-timezone interpretation, use parse_schedule_time_with_zone().

    Raises
    ------
    ValueError  If string cannot be parsed or time is not sufficiently in the future.
    """
    dt = _parse_dt_string(schedule_str)

    # If no tzinfo, treat as UTC (not server local time — avoids DST ambiguity)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    # Normalise to UTC
    dt_utc = dt.astimezone(timezone.utc)

    _validate_future(dt_utc)
    return dt_utc


def parse_schedule_time_with_zone(schedule_str: str, iana_zone: str) -> datetime:
    """
    Parse a bare datetime string and interpret it in the given IANA timezone.

    This is the DST-correct way to handle user input from a UI that sends both
    a local datetime string and the user's IANA timezone name separately.

    Example
    -------
        parse_schedule_time_with_zone("2026-09-05T18:00:00", "Asia/Karachi")
        # → 2026-09-05 13:00:00+00:00 (UTC)

        parse_schedule_time_with_zone("2026-03-08T02:30:00", "America/New_York")
        # → DST-correct UTC conversion for New York

    Raises
    ------
    ValueError          If datetime string is invalid or time is in the past.
    ZoneInfoNotFoundError  If IANA zone name is unknown.
    """
    dt = _parse_dt_string(schedule_str)

    if dt.tzinfo is not None:
        # Already has an offset — just normalise to UTC, ignore iana_zone
        dt_utc = dt.astimezone(timezone.utc)
    else:
        try:
            tz = ZoneInfo(iana_zone)
        except ZoneInfoNotFoundError:
            raise ValueError(
                f"Unknown timezone: '{iana_zone}'. "
                "Use an IANA timezone name such as 'Asia/Karachi' or 'America/New_York'."
            )
        # fold=0: if the wall clock time is ambiguous (DST fall-back), pick the
        # first (pre-DST-end) occurrence — conservative choice.
        dt_local = dt.replace(tzinfo=tz, fold=0)
        dt_utc = dt_local.astimezone(timezone.utc)

    _validate_future(dt_utc)
    return dt_utc


def format_for_youtube(dt: datetime) -> str:
    """
    Format a UTC-aware datetime as a YouTube-compatible RFC 3339 string.

    Returns e.g. "2026-09-05T13:00:00.000Z"
    """
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def validate_schedule_string(schedule_str: str) -> tuple[bool, str]:
    """
    Validate a schedule string without raising.

    Returns (True, "") on success, or (False, error_message) on failure.
    Useful for API request validation.
    """
    try:
        parse_schedule_time(schedule_str)
        return True, ""
    except ValueError as e:
        return False, str(e)


# ── Internal helpers ───────────────────────────────────────────────────────────

_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
]


def _parse_dt_string(s: str) -> datetime:
    """Parse a datetime string; return naive or tz-aware datetime."""
    s = s.strip()
    # fromisoformat handles "Z", "+HH:MM" offsets (Python 3.11+ natively).
    # For 3.9/3.10 we normalise "Z" → "+00:00" first.
    normalized = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    # Fall back to strptime for non-ISO formats
    for fmt in _FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    raise ValueError(
        f"Cannot parse datetime: '{s}'\n"
        "Expected ISO 8601 format, e.g.:\n"
        "  2026-09-05T18:00:00        (treated as UTC)\n"
        "  2026-09-05T13:00:00+05:00  (explicit UTC+5)\n"
        "  2026-09-05T18:00:00Z       (UTC)"
    )


def _validate_future(dt_utc: datetime) -> None:
    """Raise ValueError if dt_utc is not far enough in the future."""
    now_utc = datetime.now(timezone.utc)
    lead = (dt_utc - now_utc).total_seconds()
    if lead <= MIN_LEAD_SECONDS:
        raise ValueError(
            f"Scheduled time must be at least {MIN_LEAD_SECONDS} seconds in the future.\n"
            f"  Provided : {dt_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"  Now      : {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"  Gap      : {int(lead)} seconds"
        )

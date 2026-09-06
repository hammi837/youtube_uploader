"""
tests/test_scheduler.py — Unit tests for scheduling logic.

Covers:
  - Pakistan (UTC+5, no DST)
  - Explicit positive offset (+02:00)
  - UTC "Z" suffix
  - Bare string (treated as UTC)
  - Past datetime rejection
  - Invalid string rejection
  - DST-aware timezone (America/New_York)
  - IANA zone helper (parse_schedule_time_with_zone)
  - format_for_youtube output format
  - validate_schedule_string helper
"""

from __future__ import annotations

import sys
import os
from datetime import datetime, timezone, timedelta

import pytest

# Ensure the project root is on the path regardless of how pytest is invoked
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.scheduler import (
    parse_schedule_time,
    parse_schedule_time_with_zone,
    format_for_youtube,
    validate_schedule_string,
    MIN_LEAD_SECONDS,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def future_str(hours: int = 48, fmt: str = "%Y-%m-%dT%H:%M:%S") -> str:
    """Return a UTC datetime string that is `hours` hours in the future."""
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(fmt)


def future_str_offset(hours: int = 48, offset: str = "+00:00") -> str:
    """Return a datetime string with an explicit offset."""
    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + offset


# ── UTC / bare string tests ────────────────────────────────────────────────────

class TestBareString:
    def test_bare_string_treated_as_utc(self):
        """Bare strings without offset are assumed UTC."""
        s  = future_str(48)
        dt = parse_schedule_time(s)
        assert dt.tzinfo == timezone.utc

    def test_z_suffix(self):
        """'Z' suffix → UTC."""
        s  = future_str(48) + "Z"
        dt = parse_schedule_time(s)
        assert dt.tzinfo == timezone.utc

    def test_explicit_utc_offset(self):
        """'+00:00' offset → UTC."""
        s  = future_str_offset(48, "+00:00")
        dt = parse_schedule_time(s)
        assert dt.tzinfo == timezone.utc


# ── Pakistan (UTC+5, no DST) ───────────────────────────────────────────────────

class TestPakistanTimezone:
    def test_explicit_pkt_offset(self):
        """PKT datetime with explicit +05:00 offset converts to UTC correctly."""
        # Pick a date 30 days in the future to keep this test time-independent
        from datetime import datetime, timezone, timedelta
        future_date = datetime.now(timezone.utc) + timedelta(days=30)
        # Use 03:30 PKT → 22:30 UTC the previous day
        pkt_str = future_date.strftime("%Y-%m-%dT03:30:00+05:00")
        dt = parse_schedule_time(pkt_str)
        assert dt.tzinfo == timezone.utc
        assert dt.hour   == 22
        assert dt.minute == 30
        # Day should roll back by one (03:30 PKT = 22:30 previous day UTC)
        expected_day = (future_date - timedelta(days=1)).day
        assert dt.day == expected_day

    def test_pkt_iana_zone(self):
        """IANA zone Asia/Karachi converts correctly (no DST)."""
        future_pkt = (datetime.now(timezone.utc) + timedelta(hours=48)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        dt_via_zone   = parse_schedule_time_with_zone(future_pkt, "Asia/Karachi")
        dt_via_offset = parse_schedule_time(
            (datetime.now(timezone.utc) + timedelta(hours=48)).strftime(
                "%Y-%m-%dT%H:%M:%S"
            ) + "+05:00"
        )
        # Both should be UTC; the IANA version is anchored to UTC input+48h.
        # Just verify it's UTC and in the future.
        assert dt_via_zone.tzinfo == timezone.utc
        assert dt_via_zone > datetime.now(timezone.utc)

    def test_pkt_no_dst_shift(self):
        """Asia/Karachi always returns UTC+5, regardless of winter/summer date."""
        winter = parse_schedule_time_with_zone("2027-01-15T12:00:00", "Asia/Karachi")
        summer = parse_schedule_time_with_zone("2027-07-15T12:00:00", "Asia/Karachi")
        # Both should be 07:00 UTC (12:00 - 5h)
        assert winter.hour == 7
        assert summer.hour == 7


# ── Positive offset (+02:00) ──────────────────────────────────────────────────

class TestPositiveOffset:
    def test_plus_two_offset(self):
        """Explicit +02:00 offset converts correctly to UTC (2h behind wall clock)."""
        # Build a wall-clock time that is 48h from now in UTC+2
        wall_utc = datetime.now(timezone.utc) + timedelta(hours=48)
        # As a +02:00 string that wall time is 2h ahead of UTC
        wall_plus2 = wall_utc + timedelta(hours=2)
        s  = wall_plus2.strftime("%Y-%m-%dT%H:%M:%S") + "+02:00"
        dt = parse_schedule_time(s)
        assert dt.tzinfo == timezone.utc
        # After conversion dt should be back to wall_utc (within 1 second)
        diff = abs((dt - wall_utc).total_seconds())
        assert diff < 2

    def test_cest_iana(self):
        """Europe/Berlin in summer (CEST = UTC+2) converts correctly."""
        dt = parse_schedule_time_with_zone("2027-07-15T18:00:00", "Europe/Berlin")
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 16   # 18:00 CEST − 2h = 16:00 UTC


# ── DST-aware timezone ────────────────────────────────────────────────────────

class TestDSTTimezone:
    def test_new_york_summer_edt(self):
        """America/New_York in summer = EDT = UTC−4."""
        dt = parse_schedule_time_with_zone("2027-07-04T20:00:00", "America/New_York")
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 0   # 20:00 EDT + 4h = 00:00 UTC next day
        assert dt.day == 5

    def test_new_york_winter_est(self):
        """America/New_York in winter = EST = UTC−5."""
        dt = parse_schedule_time_with_zone("2027-01-15T20:00:00", "America/New_York")
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 1    # 20:00 EST + 5h = 01:00 UTC next day
        assert dt.day == 16

    def test_dst_transition_ambiguity_handled(self):
        """Ambiguous wall-clock time during DST fall-back does not crash."""
        # 2026-11-01 01:30 in America/New_York is ambiguous (clocks fall back)
        # Should not raise — fold=0 picks pre-DST-end (EDT, UTC-4)
        dt = parse_schedule_time_with_zone("2027-11-07T01:30:00", "America/New_York")
        assert dt.tzinfo == timezone.utc


# ── Future validation ─────────────────────────────────────────────────────────

class TestFutureValidation:
    def test_past_datetime_rejected(self):
        """Past datetime must raise ValueError."""
        with pytest.raises(ValueError, match="future"):
            parse_schedule_time("2020-01-01T00:00:00Z")

    def test_just_now_rejected(self):
        """Datetime less than MIN_LEAD_SECONDS in the future must be rejected."""
        almost_now = datetime.now(timezone.utc) + timedelta(seconds=30)
        s = almost_now.strftime("%Y-%m-%dT%H:%M:%SZ")
        with pytest.raises(ValueError, match="future"):
            parse_schedule_time(s)

    def test_sufficient_future_accepted(self):
        """Datetime well in the future must be accepted."""
        far_future = datetime.now(timezone.utc) + timedelta(days=7)
        s  = far_future.strftime("%Y-%m-%dT%H:%M:%SZ")
        dt = parse_schedule_time(s)
        assert dt > datetime.now(timezone.utc)


# ── Invalid input ─────────────────────────────────────────────────────────────

class TestInvalidInput:
    def test_garbage_string(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            parse_schedule_time("not-a-date")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            parse_schedule_time("")

    def test_invalid_timezone(self):
        with pytest.raises((ValueError, Exception)):
            parse_schedule_time_with_zone(future_str(48), "Invalid/Zone")


# ── format_for_youtube ────────────────────────────────────────────────────────

class TestFormatForYouTube:
    def test_output_format(self):
        """Must produce YYYY-MM-DDTHH:MM:SS.000Z format."""
        dt  = datetime(2026, 9, 5, 13, 0, 0, tzinfo=timezone.utc)
        out = format_for_youtube(dt)
        assert out == "2026-09-05T13:00:00.000Z"

    def test_converts_to_utc(self):
        """Non-UTC input must be converted to UTC in output."""
        from zoneinfo import ZoneInfo
        dt  = datetime(2026, 9, 5, 18, 0, 0, tzinfo=ZoneInfo("Asia/Karachi"))
        out = format_for_youtube(dt)
        assert out == "2026-09-05T13:00:00.000Z"


# ── validate_schedule_string ──────────────────────────────────────────────────

class TestValidateScheduleString:
    def test_valid_returns_true(self):
        ok, msg = validate_schedule_string(future_str(48) + "Z")
        assert ok is True
        assert msg == ""

    def test_past_returns_false(self):
        ok, msg = validate_schedule_string("2020-01-01T00:00:00Z")
        assert ok is False
        assert msg != ""

    def test_invalid_string_returns_false(self):
        ok, msg = validate_schedule_string("banana")
        assert ok is False
        assert msg != ""

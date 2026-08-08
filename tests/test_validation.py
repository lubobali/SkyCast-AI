"""Argument cleaning. The caller is a language model, so the inputs that turn
up are confident guesses rather than typos."""

from __future__ import annotations

from datetime import date

import pytest
from validation import (
    MAX_COMPARE_LOCATIONS,
    BadArgument,
    clean_date,
    clean_days,
    clean_limit,
    clean_location,
    clean_locations,
)


class TestLocation:
    def test_strips_whitespace(self):
        assert clean_location("  Chicago, IL \n") == "Chicago, IL"

    def test_rejects_empty(self):
        with pytest.raises(BadArgument, match="must not be empty"):
            clean_location("   ")

    def test_rejects_non_string(self):
        with pytest.raises(BadArgument, match="must be text"):
            clean_location(41.88)

    def test_does_not_validate_the_format_here(self):
        # parse_location owns that, and owns the message that teaches the
        # accepted forms. Two places to keep in step would be one too many.
        assert clean_location("Springfield") == "Springfield"


class TestLocations:
    def test_accepts_a_list(self):
        assert clean_locations(["Chicago, IL", "Austin, TX"]) == ["Chicago, IL", "Austin, TX"]

    def test_accepts_a_semicolon_string(self):
        # Agents pass both. Rejecting one produces a retry loop, not an answer.
        assert clean_locations("Chicago, IL;Austin, TX") == ["Chicago, IL", "Austin, TX"]

    def test_deduplicates_case_insensitively(self):
        assert clean_locations(["Chicago, IL", "chicago, il", "Austin, TX"]) == [
            "Chicago, IL", "Austin, TX",
        ]

    def test_caps_the_count(self):
        many = [f"City{index}, IL" for index in range(12)]
        assert len(clean_locations(many)) == MAX_COMPARE_LOCATIONS

    def test_requires_at_least_two(self):
        with pytest.raises(BadArgument, match="at least two"):
            clean_locations(["Chicago, IL"])

    def test_rejects_empty(self):
        with pytest.raises(BadArgument):
            clean_locations([])

    def test_rejects_a_number(self):
        with pytest.raises(BadArgument, match="must be a list"):
            clean_locations(42)


class TestDays:
    def test_default_when_absent(self):
        assert clean_days(None) == 3
        assert clean_days("") == 3

    def test_accepts_a_numeric_string(self):
        assert clean_days("7") == 7

    def test_clamps_high(self):
        # Open-Meteo answers 400 above 16, and that 400 tells the agent nothing
        # about what it should have asked for.
        assert clean_days(30) == 16

    def test_clamps_low(self):
        assert clean_days(0) == 1
        assert clean_days(-5) == 1

    def test_rejects_words(self):
        with pytest.raises(BadArgument, match="whole number"):
            clean_days("three")


class TestLimit:
    def test_default(self):
        assert clean_limit(None) == 10

    def test_clamped_both_ways(self):
        assert clean_limit(500) == 20
        assert clean_limit(0) == 1

    def test_rejects_words(self):
        with pytest.raises(BadArgument):
            clean_limit("lots")


class TestDate:
    def test_none_means_the_first_available_day(self):
        assert clean_date(None) is None
        assert clean_date("") is None

    def test_iso(self):
        assert clean_date("2026-08-09") == "2026-08-09"

    def test_single_digit_parts_are_normalized(self):
        # A model reaches for "2026-8-9" often enough to be worth accepting.
        assert clean_date("2026-8-9") == "2026-08-09"

    def test_a_real_date_object(self):
        assert clean_date(date(2026, 8, 9)) == "2026-08-09"

    def test_rejects_relative_words(self):
        # "Tomorrow" depends on the timezone where the weather is, which is not
        # known until after the geocode. A server that assumes UTC is wrong for
        # exactly the evening hours people ask about.
        with pytest.raises(BadArgument, match="tomorrow"):
            clean_date("tomorrow")

    def test_the_rejection_says_what_to_do_instead(self):
        with pytest.raises(BadArgument) as caught:
            clean_date("next saturday")
        assert "get_forecast" in str(caught.value)

    def test_rejects_an_impossible_date(self):
        with pytest.raises(BadArgument, match="not a real date"):
            clean_date("2026-02-30")

    def test_rejects_a_non_string(self):
        with pytest.raises(BadArgument, match="must be text"):
            clean_date(20260809)

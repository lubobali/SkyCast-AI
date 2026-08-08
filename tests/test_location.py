"""Location parsing: the only input a user types freely, so the only one that
can be wrong in interesting ways."""

from __future__ import annotations

import pytest
from location import COORD_PRECISION, LocationError, parse_location


class TestCityAndState:
    def test_reads_a_two_letter_state(self):
        parsed = parse_location("Chicago, IL")
        assert (parsed.city, parsed.state, parsed.is_coordinates) == ("Chicago", "IL", False)

    def test_reads_a_spelled_out_state(self):
        assert parse_location("Chicago, Illinois").state == "IL"

    def test_state_case_and_spacing_do_not_matter(self):
        assert parse_location("  austin ,   texas  ").state == "TX"

    def test_multi_word_city(self):
        parsed = parse_location("St Petersburg, FL")
        assert parsed.city == "St Petersburg"

    def test_territories_are_states_too(self):
        # NWS issues real alerts for these, so rejecting them would lose
        # exactly the users most likely to need a severe-weather tool.
        assert parse_location("San Juan, Puerto Rico").state == "PR"

    def test_label_is_normalized(self):
        assert parse_location("chicago, Illinois").label == "chicago, IL"


class TestCoordinates:
    def test_reads_a_coordinate_pair(self):
        parsed = parse_location("41.8781,-87.6298")
        assert parsed.is_coordinates
        assert (parsed.latitude, parsed.longitude) == (41.8781, -87.6298)

    def test_spaces_around_the_comma_are_fine(self):
        assert parse_location(" 41.8781 , -87.6298 ").latitude == 41.8781

    def test_precision_is_capped(self):
        # api.weather.gov answers 301 above four decimals. Rounding here costs
        # about eleven metres and saves a redirect on every single lookup.
        parsed = parse_location("41.87811111,-87.62988888")
        assert parsed.latitude == round(41.87811111, COORD_PRECISION)
        assert parsed.longitude == round(-87.62988888, COORD_PRECISION)

    def test_integer_coordinates_still_parse(self):
        assert parse_location("41,-87").is_coordinates

    @pytest.mark.parametrize("bad", ["91.0,-87.6", "-90.1,0"])
    def test_latitude_out_of_range_is_rejected(self, bad):
        with pytest.raises(LocationError, match="Latitude"):
            parse_location(bad)

    @pytest.mark.parametrize("bad", ["41.8,181.0", "41.8,-180.1"])
    def test_longitude_out_of_range_is_rejected(self, bad):
        with pytest.raises(LocationError, match="Longitude"):
            parse_location(bad)


class TestRejections:
    def test_a_bare_city_is_rejected(self):
        # The whole point. "Springfield" names places in over thirty states,
        # and guessing one returns confident weather for the wrong place -
        # a wrong answer that looks exactly like a right one.
        with pytest.raises(LocationError):
            parse_location("Springfield")

    def test_an_unknown_state_is_rejected(self):
        with pytest.raises(LocationError, match="not a US state"):
            parse_location("Toronto, Ontario")

    def test_empty_is_rejected(self):
        with pytest.raises(LocationError, match="must not be empty"):
            parse_location("   ")

    def test_non_string_is_rejected(self):
        with pytest.raises(LocationError, match="must be text"):
            parse_location(41.88)

    def test_three_parts_is_rejected(self):
        with pytest.raises(LocationError):
            parse_location("Chicago, IL, USA")

    def test_missing_city_is_rejected(self):
        with pytest.raises(LocationError):
            parse_location(", IL")

    def test_the_error_shows_the_accepted_forms(self):
        # This message is handed straight to the agent, which hands it to the
        # user. It has to say what to type next, not just that it failed.
        with pytest.raises(LocationError) as caught:
            parse_location("Springfield")
        assert "City, ST" in str(caught.value)

"""Tests for the pure parsing functions in lookup.py."""

from lookup import (
    Sighting,
    _extract_record_count,
    _parse_detail_page,
    _parse_search_results_from_html,
)


# ---------------------------------------------------------------------------
# _parse_search_results_from_html
# ---------------------------------------------------------------------------

class TestParseSearchResults:
    def test_match_returns_one_sighting(self, html_search_match):
        sightings = _parse_search_results_from_html(html_search_match)
        assert len(sightings) == 1

    def test_match_date(self, html_search_match):
        sightings = _parse_search_results_from_html(html_search_match)
        assert sightings[0].date == "SAT JAN 31 2026 19:51:04 PST"

    def test_match_location(self, html_search_match):
        sightings = _parse_search_results_from_html(html_search_match)
        assert "ST. PETER MN" in sightings[0].location

    def test_match_description_not_empty(self, html_search_match):
        sightings = _parse_search_results_from_html(html_search_match)
        assert sightings[0].description != ""

    def test_no_match_returns_empty(self, html_search_no_match):
        sightings = _parse_search_results_from_html(html_search_no_match)
        assert sightings == []

    def test_empty_string_returns_empty(self):
        assert _parse_search_results_from_html("") == []

    def test_multiple_date_blocks(self):
        html = (
            '<font style=font-size:9pt; color=#c0c0c0>\n'
            'MON FEB 1 2026 10:00:00 PST\n'
            '<tr></td><td>\n'
            '<img src=mapmarker.png width=15> CITY A\n'
            '<font style=font-size:9pt;>\nDesc A\n'
            '<!--SPLIT-->'
            '<font style=font-size:9pt; color=#c0c0c0>\n'
            'TUE FEB 2 2026 11:00:00 PST\n'
            '<tr></td><td>\n'
            '<img src=mapmarker.png width=15> CITY B\n'
            '<font style=font-size:9pt;>\nDesc B\n'
            '<!--RESULT:2-->'
        )
        sightings = _parse_search_results_from_html(html)
        assert len(sightings) == 2
        assert sightings[0].date == "MON FEB 1 2026 10:00:00 PST"
        assert sightings[1].date == "TUE FEB 2 2026 11:00:00 PST"

    def test_more_records_not_captured_as_description(self, html_search_match):
        sightings = _parse_search_results_from_html(html_search_match)
        for s in sightings:
            assert "more records" not in s.description.lower()

    def test_location_from_mapmarker(self, html_search_match):
        sightings = _parse_search_results_from_html(html_search_match)
        assert sightings[0].location != ""


# ---------------------------------------------------------------------------
# _parse_detail_page
# ---------------------------------------------------------------------------

class TestParseDetailPage:
    def test_returns_sightings(self, html_detail_page):
        sightings = _parse_detail_page(html_detail_page)
        assert len(sightings) >= 1

    def test_date_populated(self, html_detail_page):
        sightings = _parse_detail_page(html_detail_page)
        for s in sightings:
            assert s.date != ""

    def test_location_populated(self, html_detail_page):
        sightings = _parse_detail_page(html_detail_page)
        for s in sightings:
            assert s.location != ""

    def test_close_button_not_in_location(self, html_detail_page):
        sightings = _parse_detail_page(html_detail_page)
        for s in sightings:
            assert "\u00d7" not in s.location  # ×

    def test_unconfirmed_not_in_description(self, html_detail_page):
        sightings = _parse_detail_page(html_detail_page)
        for s in sightings:
            assert s.description != "UNCONFIRMED"

    def test_upcoming_action_not_in_description(self, html_detail_page):
        sightings = _parse_detail_page(html_detail_page)
        for s in sightings:
            assert "upcoming action" not in s.description.lower()

    def test_time_field_extracted(self, html_detail_page):
        sightings = _parse_detail_page(html_detail_page)
        has_time = any(s.time for s in sightings)
        assert has_time

    def test_vehicle_extracted_from_crafted_html(self):
        """Vehicle extraction requires a specific sibling-table structure.

        The snapshot HTML doesn't have the exact structure the parser expects for
        vehicle extraction (the vehicle table isn't a direct previous sibling of
        the created: table), so we test with crafted HTML.
        """
        html = """
        <font style="font-size:18pt;" color="#555"><b>JAN 1 2026</b></font>
        <font color="red">SOMEWHERE</font>
        <font style="font-size:14pt;">A description</font>
        <table cellpadding="0"><tr><td>HONDA CIVIC</td></tr></table>
        <table cellpadding="0"><tr><td><font style="font-size:9pt;">created: MON JAN 1 2026 12:00:00 PST</font></td></tr></table>
        """
        sightings = _parse_detail_page(html)
        assert len(sightings) == 1
        assert sightings[0].vehicle == "HONDA CIVIC"

    def test_description_populated(self, html_detail_page):
        sightings = _parse_detail_page(html_detail_page)
        has_desc = any(s.description for s in sightings)
        assert has_desc

    def test_empty_string_returns_empty(self):
        assert _parse_detail_page("") == []


# ---------------------------------------------------------------------------
# _extract_record_count
# ---------------------------------------------------------------------------

class TestExtractRecordCount:
    def test_more_records_present(self):
        html = '<table>2  more records</table>'
        assert _extract_record_count(html, shown=1) == 3

    def test_no_more_records(self):
        html = '<table>some other text</table>'
        assert _extract_record_count(html, shown=1) == 1

    def test_case_insensitive(self):
        html = '<table>5 More Records</table>'
        assert _extract_record_count(html, shown=1) == 6

    def test_zero_shown_with_more(self):
        html = '<table>10 more records</table>'
        assert _extract_record_count(html, shown=0) == 10

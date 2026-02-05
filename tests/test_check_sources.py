"""Tests for check_sources.py — health-check script orchestration and validation."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from lookup import LookupResult, Sighting
from check_sources import (
    check_stopice_search,
    check_stopice_detail,
    check_defrost_meta,
    check_defrost_pages,
    check_defrost_stopice_json,
    check_defrost_full_lookup,
    main,
)


# ---------------------------------------------------------------------------
# main() — argument parsing and exit codes
# ---------------------------------------------------------------------------

class TestMain:
    @patch("check_sources.close_session", new_callable=AsyncMock)
    @patch("check_sources.clear_caches")
    async def test_no_plate_exits_2(self, _caches, _session):
        with patch("sys.argv", ["check_sources.py"]):
            result = await main()
        assert result == 2

    @patch("check_sources.close_session", new_callable=AsyncMock)
    @patch("check_sources.clear_caches")
    @patch("check_sources.check_defrost_full_lookup", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_defrost_stopice_json", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_defrost_pages", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_defrost_meta", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_stopice_detail", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_stopice_search", new_callable=AsyncMock, return_value=True)
    async def test_all_pass_exits_0(self, *_mocks):
        with patch("sys.argv", ["check_sources.py", "ABC123"]):
            result = await main()
        assert result == 0

    @patch("check_sources.close_session", new_callable=AsyncMock)
    @patch("check_sources.clear_caches")
    @patch("check_sources.check_defrost_full_lookup", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_defrost_stopice_json", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_defrost_pages", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_defrost_meta", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_stopice_detail", new_callable=AsyncMock, return_value=False)
    @patch("check_sources.check_stopice_search", new_callable=AsyncMock, return_value=True)
    async def test_one_failure_exits_1(self, *_mocks):
        with patch("sys.argv", ["check_sources.py", "ABC123"]):
            result = await main()
        assert result == 1

    @patch("check_sources.close_session", new_callable=AsyncMock)
    @patch("check_sources.clear_caches")
    @patch("check_sources.check_defrost_full_lookup", new_callable=AsyncMock, return_value=None)
    @patch("check_sources.check_defrost_stopice_json", new_callable=AsyncMock, return_value=None)
    @patch("check_sources.check_defrost_pages", new_callable=AsyncMock, return_value=None)
    @patch("check_sources.check_defrost_meta", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_stopice_detail", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_stopice_search", new_callable=AsyncMock, return_value=True)
    async def test_skips_do_not_count_as_failures(self, *_mocks):
        with patch("sys.argv", ["check_sources.py", "ABC123"]):
            result = await main()
        assert result == 0

    @patch("check_sources.close_session", new_callable=AsyncMock)
    @patch("check_sources.clear_caches")
    @patch("check_sources.check_defrost_full_lookup", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_defrost_stopice_json", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_defrost_pages", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_defrost_meta", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_stopice_detail", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_stopice_search", new_callable=AsyncMock, return_value=True)
    async def test_env_var_fallback(self, *_mocks):
        with patch("sys.argv", ["check_sources.py"]), \
             patch.dict("os.environ", {"CHECK_PLATE": "XYZ789"}):
            result = await main()
        assert result == 0

    @patch("check_sources.close_session", new_callable=AsyncMock)
    @patch("check_sources.clear_caches")
    @patch("check_sources.check_defrost_full_lookup", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_defrost_stopice_json", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_defrost_pages", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_defrost_meta", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_stopice_detail", new_callable=AsyncMock, return_value=True)
    @patch("check_sources.check_stopice_search", new_callable=AsyncMock, return_value=True)
    async def test_plate_uppercased(self, mock_search, *_mocks):
        with patch("sys.argv", ["check_sources.py", "abc123"]):
            await main()
        mock_search.assert_called_once_with("ABC123")


# ---------------------------------------------------------------------------
# check_stopice_search
# ---------------------------------------------------------------------------

class TestCheckStopiceSearch:
    @patch("check_sources.check_plate", new_callable=AsyncMock)
    async def test_pass(self, mock_cp):
        mock_cp.return_value = LookupResult(
            found=True, match_count=1,
            sightings=[Sighting(date="Jan 1", location="A")],
        )
        assert await check_stopice_search("TEST") is True

    @patch("check_sources.check_plate", new_callable=AsyncMock)
    async def test_fail_error(self, mock_cp):
        mock_cp.return_value = LookupResult(found=False, error="timeout")
        assert await check_stopice_search("TEST") is False

    @patch("check_sources.check_plate", new_callable=AsyncMock)
    async def test_fail_not_found(self, mock_cp):
        mock_cp.return_value = LookupResult(found=False)
        assert await check_stopice_search("TEST") is False

    @patch("check_sources.check_plate", new_callable=AsyncMock)
    async def test_fail_missing_date(self, mock_cp):
        mock_cp.return_value = LookupResult(
            found=True, match_count=1,
            sightings=[Sighting(date="", location="A")],
        )
        assert await check_stopice_search("TEST") is False

    @patch("check_sources.check_plate", new_callable=AsyncMock)
    async def test_fail_no_sightings(self, mock_cp):
        mock_cp.return_value = LookupResult(found=True, match_count=1, sightings=[])
        assert await check_stopice_search("TEST") is False


# ---------------------------------------------------------------------------
# check_stopice_detail
# ---------------------------------------------------------------------------

class TestCheckStopiceDetail:
    @patch("check_sources.fetch_descriptions", new_callable=AsyncMock)
    async def test_pass(self, mock_fd):
        mock_fd.return_value = LookupResult(
            found=True,
            sightings=[Sighting(date="Jan 1", location="A", vehicle="Honda")],
        )
        assert await check_stopice_detail("TEST") is True

    @patch("check_sources.fetch_descriptions", new_callable=AsyncMock)
    async def test_fail_error(self, mock_fd):
        mock_fd.return_value = LookupResult(found=False, error="timeout")
        assert await check_stopice_detail("TEST") is False

    @patch("check_sources.fetch_descriptions", new_callable=AsyncMock)
    async def test_fail_no_vehicle_or_desc(self, mock_fd):
        mock_fd.return_value = LookupResult(
            found=True,
            sightings=[Sighting(date="Jan 1", location="A")],
        )
        assert await check_stopice_detail("TEST") is False

    @patch("check_sources.fetch_descriptions", new_callable=AsyncMock)
    async def test_fail_missing_location(self, mock_fd):
        mock_fd.return_value = LookupResult(
            found=True,
            sightings=[Sighting(date="Jan 1", location="", vehicle="Honda")],
        )
        assert await check_stopice_detail("TEST") is False


# ---------------------------------------------------------------------------
# check_defrost_meta
# ---------------------------------------------------------------------------

class TestCheckDefrostMeta:
    @patch("check_sources.fetch_meta", new_callable=AsyncMock)
    async def test_pass(self, mock_fm):
        mock_fm.return_value = (
            {"rotation": 2, "numPages": 5, "updated": "2026-02-01T00:00:00Z"},
            None,
        )
        assert await check_defrost_meta() is True

    @patch("check_sources.fetch_meta", new_callable=AsyncMock)
    async def test_fail_error(self, mock_fm):
        mock_fm.return_value = (None, "Connection error")
        assert await check_defrost_meta() is False

    @patch("check_sources.fetch_meta", new_callable=AsyncMock)
    async def test_fail_missing_rotation(self, mock_fm):
        mock_fm.return_value = ({"numPages": 5, "updated": "2026-02-01"}, None)
        assert await check_defrost_meta() is False

    @patch("check_sources.fetch_meta", new_callable=AsyncMock)
    async def test_fail_num_pages_zero(self, mock_fm):
        mock_fm.return_value = (
            {"rotation": 1, "numPages": 0, "updated": "2026-02-01"},
            None,
        )
        assert await check_defrost_meta() is False

    @patch("check_sources.fetch_meta", new_callable=AsyncMock)
    async def test_fail_empty_updated(self, mock_fm):
        mock_fm.return_value = (
            {"rotation": 1, "numPages": 5, "updated": ""},
            None,
        )
        assert await check_defrost_meta() is False


# ---------------------------------------------------------------------------
# check_defrost_pages
# ---------------------------------------------------------------------------

class TestCheckDefrostPages:
    @patch("check_sources.fetch_all_pages", new_callable=AsyncMock)
    @patch("check_sources.fetch_meta", new_callable=AsyncMock)
    @patch("check_sources.get_decrypt_key", return_value="testkey")
    async def test_pass(self, _key, mock_meta, mock_pages):
        mock_meta.return_value = ({"rotation": 1, "numPages": 1}, None)
        mock_pages.return_value = (
            [{"fields": {"Plate": "ABC123"}}],
            [],
        )
        assert await check_defrost_pages() is True

    @patch("check_sources.get_decrypt_key", return_value="")
    async def test_skip_no_key(self, _key):
        assert await check_defrost_pages() is None

    @patch("check_sources.fetch_all_pages", new_callable=AsyncMock)
    @patch("check_sources.fetch_meta", new_callable=AsyncMock)
    @patch("check_sources.get_decrypt_key", return_value="testkey")
    async def test_fail_page_errors(self, _key, mock_meta, mock_pages):
        mock_meta.return_value = ({"rotation": 1, "numPages": 2}, None)
        mock_pages.return_value = ([], ["Page 1: decryption failed"])
        assert await check_defrost_pages() is False

    @patch("check_sources.fetch_all_pages", new_callable=AsyncMock)
    @patch("check_sources.fetch_meta", new_callable=AsyncMock)
    @patch("check_sources.get_decrypt_key", return_value="testkey")
    async def test_fail_missing_fields_plate(self, _key, mock_meta, mock_pages):
        mock_meta.return_value = ({"rotation": 1, "numPages": 1}, None)
        mock_pages.return_value = ([{"fields": {}}], [])
        assert await check_defrost_pages() is False

    @patch("check_sources.fetch_meta", new_callable=AsyncMock)
    @patch("check_sources.get_decrypt_key", return_value="testkey")
    async def test_fail_meta_error(self, _key, mock_meta):
        mock_meta.return_value = (None, "Connection error")
        assert await check_defrost_pages() is False


# ---------------------------------------------------------------------------
# check_defrost_stopice_json
# ---------------------------------------------------------------------------

class TestCheckDefrostStopiceJson:
    @patch("check_sources.fetch_with_retry", new_callable=AsyncMock)
    @patch("check_sources.get_defrost_url", return_value="https://example.com/plates.json")
    async def test_pass(self, _url, mock_fetch):
        data = {"plates": [{"license_plate": "ABC", "records": []}]}
        mock_fetch.return_value = (json.dumps(data), None)
        assert await check_defrost_stopice_json() is True

    @patch("check_sources.get_defrost_url", return_value="")
    async def test_skip_no_url(self, _url):
        assert await check_defrost_stopice_json() is None

    @patch("check_sources.fetch_with_retry", new_callable=AsyncMock)
    @patch("check_sources.get_defrost_url", return_value="https://example.com/plates.json")
    async def test_fail_fetch_error(self, _url, mock_fetch):
        mock_fetch.return_value = (None, "Connection error")
        assert await check_defrost_stopice_json() is False

    @patch("check_sources.fetch_with_retry", new_callable=AsyncMock)
    @patch("check_sources.get_defrost_url", return_value="https://example.com/plates.json")
    async def test_fail_invalid_json(self, _url, mock_fetch):
        mock_fetch.return_value = ("not json{{{", None)
        assert await check_defrost_stopice_json() is False

    @patch("check_sources.fetch_with_retry", new_callable=AsyncMock)
    @patch("check_sources.get_defrost_url", return_value="https://example.com/plates.json")
    async def test_fail_missing_license_plate(self, _url, mock_fetch):
        data = {"plates": [{"records": []}]}
        mock_fetch.return_value = (json.dumps(data), None)
        assert await check_defrost_stopice_json() is False


# ---------------------------------------------------------------------------
# check_defrost_full_lookup
# ---------------------------------------------------------------------------

class TestCheckDefrostFullLookup:
    @patch("check_sources.check_plate_defrost", new_callable=AsyncMock)
    @patch("check_sources.get_defrost_url", return_value="")
    @patch("check_sources.get_decrypt_key", return_value="testkey")
    async def test_pass_found(self, _key, _url, mock_cpd):
        mock_cpd.return_value = LookupResult(
            found=True, match_count=1,
            sightings=[Sighting(date="Jan 1", location="A")],
        )
        assert await check_defrost_full_lookup("TEST") is True

    @patch("check_sources.check_plate_defrost", new_callable=AsyncMock)
    @patch("check_sources.get_defrost_url", return_value="")
    @patch("check_sources.get_decrypt_key", return_value="testkey")
    async def test_pass_not_found(self, _key, _url, mock_cpd):
        mock_cpd.return_value = LookupResult(found=False)
        assert await check_defrost_full_lookup("TEST") is True

    @patch("check_sources.get_defrost_url", return_value="")
    @patch("check_sources.get_decrypt_key", return_value="")
    async def test_skip_no_env_vars(self, _key, _url):
        assert await check_defrost_full_lookup("TEST") is None

    @patch("check_sources.check_plate_defrost", new_callable=AsyncMock)
    @patch("check_sources.get_defrost_url", return_value="https://example.com")
    @patch("check_sources.get_decrypt_key", return_value="")
    async def test_fail_error(self, _key, _url, mock_cpd):
        mock_cpd.return_value = LookupResult(found=False, error="decrypt failed")
        assert await check_defrost_full_lookup("TEST") is False

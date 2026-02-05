"""Tests for lookup_defrost.py — defrostmn.net plate lookup with paginated encrypted data."""

import json
import os
import time
from unittest.mock import AsyncMock, patch

import pytest

import lookup_defrost
from lookup import LookupResult, Sighting
from lookup_defrost import (
    _check_paginated_plates,
    _check_stopice_fallback,
    _decrypt_page,
    _get_cache_dir,
    _load_cache,
    _save_cache,
    fetch_all_pages,
    fetch_meta,
    _format_date,
    _format_iso_date,
    _merge_results,
    _record_to_sighting,
    _search_paginated_plates,
    _search_stopice_plates,
    check_plate_defrost,
)
from tests.conftest import _TEST_PASSWORD


# ---------------------------------------------------------------------------
# _format_date (stopice legacy format)
# ---------------------------------------------------------------------------

class TestFormatDate:
    def test_all_fields_present(self):
        record = {"month": "JAN", "day": "15", "year": "2026", "datestamp": "fallback"}
        assert _format_date(record) == "JAN 15, 2026"

    def test_missing_month_falls_back(self):
        record = {"day": "15", "year": "2026", "datestamp": "WED JAN 15 2026"}
        assert _format_date(record) == "WED JAN 15 2026"

    def test_missing_day_falls_back(self):
        record = {"month": "JAN", "year": "2026", "datestamp": "WED JAN 15 2026"}
        assert _format_date(record) == "WED JAN 15 2026"

    def test_missing_year_falls_back(self):
        record = {"month": "JAN", "day": "15", "datestamp": "WED JAN 15 2026"}
        assert _format_date(record) == "WED JAN 15 2026"

    def test_empty_record(self):
        assert _format_date({}) == ""


# ---------------------------------------------------------------------------
# _format_iso_date
# ---------------------------------------------------------------------------

class TestFormatIsoDate:
    def test_valid_iso(self):
        assert _format_iso_date("2026-01-27T19:30:00.000Z") == "Jan 27, 2026"

    def test_empty_string(self):
        assert _format_iso_date("") == ""

    def test_invalid_string(self):
        assert _format_iso_date("not-a-date") == "not-a-date"


# ---------------------------------------------------------------------------
# _decrypt_page
# ---------------------------------------------------------------------------

class TestDecryptPage:
    def test_successful_decryption(self, defrost_encrypted_page):
        result = _decrypt_page(
            defrost_encrypted_page["encrypted"],
            defrost_encrypted_page["password"],
        )
        assert result == defrost_encrypted_page["plaintext_str"]
        data = json.loads(result)
        assert data["records"][0]["fields"]["Plate"] == "TEST123"

    def test_wrong_password(self, defrost_encrypted_page):
        with pytest.raises(Exception):
            _decrypt_page(defrost_encrypted_page["encrypted"], "wrong-password")

    def test_missing_fields(self):
        with pytest.raises(KeyError):
            _decrypt_page({"salt": "dGVzdA==", "iv": "dGVzdA=="}, "password")

    def test_bad_base64(self):
        with pytest.raises(Exception):
            _decrypt_page(
                {"salt": "!!!invalid!!!", "iv": "dGVzdA==", "ciphertext": "dGVzdA=="},
                "password",
            )


# ---------------------------------------------------------------------------
# _record_to_sighting
# ---------------------------------------------------------------------------

class TestRecordToSighting:
    def test_full_fields(self):
        fields = {
            "Last Seen": "2026-01-27T19:30:00.000Z",
            "Last Location Seen": "123 Main St",
            "Vehicle Description": "White Honda Civic",
            "Plate Status": ["Confirmed ICE"],
            "Tags": "ICE decals/insignia",
            "Reports Count": 3,
        }
        s = _record_to_sighting(fields)
        assert s.date == "Jan 27, 2026"
        assert s.location == "123 Main St"
        assert s.vehicle == "White Honda Civic"
        assert "Confirmed ICE" in s.description
        assert "ICE decals/insignia" in s.description

    def test_fallback_to_unique_vehicles(self):
        fields = {
            "Last Seen": "2026-01-27T19:30:00.000Z",
            "Unique vehicles": "Black Ford Explorer",
        }
        s = _record_to_sighting(fields)
        assert s.vehicle == "Black Ford Explorer"

    def test_minimal_fields(self):
        s = _record_to_sighting({})
        assert s.date == ""
        assert s.location == ""
        assert s.vehicle == ""
        assert s.description == ""


# ---------------------------------------------------------------------------
# fetch_meta
# ---------------------------------------------------------------------------

class TestFetchMeta:
    @patch("lookup_defrost.fetch_with_retry")
    async def test_success(self, mock_fetch):
        meta = {"rotation": 2, "numPages": 5, "updated": "2026-02-01T00:00:00Z"}
        mock_fetch.return_value = (json.dumps(meta), None)
        result, error = await fetch_meta()
        assert result == meta
        assert error is None

    @patch("lookup_defrost.fetch_with_retry")
    async def test_http_error(self, mock_fetch):
        mock_fetch.return_value = (None, "Could not reach lookup service")
        result, error = await fetch_meta()
        assert result is None
        assert error == "Could not reach lookup service"

    @patch("lookup_defrost.fetch_with_retry")
    async def test_invalid_json(self, mock_fetch):
        mock_fetch.return_value = ("not json{{{", None)
        result, error = await fetch_meta()
        assert result is None
        assert "Invalid meta JSON" in error


# ---------------------------------------------------------------------------
# fetch_all_pages
# ---------------------------------------------------------------------------

class TestFetchAllPages:
    @patch("lookup_defrost.get_decrypt_key", return_value=_TEST_PASSWORD)
    @patch("lookup_defrost.fetch_with_retry")
    async def test_all_pages_succeed(self, mock_fetch, _key, defrost_encrypted_page):
        encrypted_json = json.dumps(defrost_encrypted_page["encrypted"])
        mock_fetch.return_value = (encrypted_json, None)

        records, errors = await fetch_all_pages(1, 2)
        assert len(records) == 2  # 1 record per page * 2 pages
        assert errors == []

    @patch("lookup_defrost.get_decrypt_key", return_value=_TEST_PASSWORD)
    @patch("lookup_defrost.fetch_with_retry")
    async def test_partial_failure(self, mock_fetch, _key, defrost_encrypted_page):
        encrypted_json = json.dumps(defrost_encrypted_page["encrypted"])
        mock_fetch.side_effect = [
            (encrypted_json, None),
            (None, "Connection error"),
        ]
        records, errors = await fetch_all_pages(1, 2)
        assert len(records) == 1
        assert len(errors) == 1
        assert "Page 2" in errors[0]

    @patch("lookup_defrost.get_decrypt_key", return_value="")
    async def test_no_decrypt_key(self, _key):
        records, errors = await fetch_all_pages(1, 2)
        assert records == []
        assert "DEFROST_DECRYPT_KEY not configured" in errors[0]

    @patch("lookup_defrost.get_decrypt_key", return_value="wrong-key")
    @patch("lookup_defrost.fetch_with_retry")
    async def test_decryption_failure(self, mock_fetch, _key, defrost_encrypted_page):
        encrypted_json = json.dumps(defrost_encrypted_page["encrypted"])
        mock_fetch.return_value = (encrypted_json, None)

        records, errors = await fetch_all_pages(1, 1)
        assert records == []
        assert len(errors) == 1
        assert "decryption failed" in errors[0]


# ---------------------------------------------------------------------------
# _search_paginated_plates
# ---------------------------------------------------------------------------

class TestSearchPaginatedPlates:
    def test_exact_match(self, defrost_page_sample):
        data = json.loads(defrost_page_sample)
        result = _search_paginated_plates(data["records"], "TEST123")
        assert result.found is True
        assert result.match_count == 1
        assert result.record_count == 3
        assert len(result.sightings) == 1
        assert result.sightings[0].location == "123 Main St, Minneapolis"
        assert result.status == "Confirmed ICE"

    def test_case_insensitive(self, defrost_page_sample):
        data = json.loads(defrost_page_sample)
        result = _search_paginated_plates(data["records"], "test123")
        assert result.found is True

    def test_no_match(self, defrost_page_sample):
        data = json.loads(defrost_page_sample)
        result = _search_paginated_plates(data["records"], "ZZZZZZ")
        assert result.found is False
        assert result.sightings == []

    def test_partial_plate_no_match(self, defrost_page_sample):
        data = json.loads(defrost_page_sample)
        result = _search_paginated_plates(data["records"], "TEST")
        assert result.found is False


# ---------------------------------------------------------------------------
# _search_stopice_plates
# ---------------------------------------------------------------------------

class TestSearchStopicePlates:
    def test_exact_match(self, defrost_json_sample):
        data = json.loads(defrost_json_sample)
        result = _search_stopice_plates(data["plates"], "TEST123")
        assert result.found is True
        assert result.match_count == 1
        assert result.record_count == 2
        assert result.sightings[0].location == "123 Main St, Minneapolis, Minnesota"

    def test_no_match(self, defrost_json_sample):
        data = json.loads(defrost_json_sample)
        result = _search_stopice_plates(data["plates"], "ZZZZZZZ")
        assert result.found is False


# ---------------------------------------------------------------------------
# _check_paginated_plates
# ---------------------------------------------------------------------------

class TestCheckPaginatedPlates:
    @patch("lookup_defrost.get_decrypt_key", return_value="testkey")
    @patch("lookup_defrost.fetch_all_pages")
    @patch("lookup_defrost.fetch_meta")
    async def test_cache_miss(self, mock_meta, mock_pages, _key, defrost_page_sample):
        data = json.loads(defrost_page_sample)
        mock_meta.return_value = (
            {"rotation": 1, "numPages": 1, "updated": "2026-02-01T00:00:00Z"},
            None,
        )
        mock_pages.return_value = (data["records"], [])

        result = await _check_paginated_plates("TEST123")
        assert result.found is True
        mock_pages.assert_called_once()

    @patch("lookup_defrost.get_decrypt_key", return_value="testkey")
    @patch("lookup_defrost.fetch_all_pages")
    @patch("lookup_defrost.fetch_meta")
    async def test_cache_hit(self, mock_meta, mock_pages, _key, defrost_page_sample):
        data = json.loads(defrost_page_sample)
        # Populate cache
        lookup_defrost._plates_cache = data["records"]
        lookup_defrost._plates_cache_updated = "2026-02-01T00:00:00Z"

        mock_meta.return_value = (
            {"rotation": 1, "numPages": 1, "updated": "2026-02-01T00:00:00Z"},
            None,
        )

        result = await _check_paginated_plates("TEST123")
        assert result.found is True
        mock_pages.assert_not_called()  # Should use cache

    @patch("lookup_defrost.get_decrypt_key", return_value="testkey")
    @patch("lookup_defrost.fetch_all_pages")
    @patch("lookup_defrost.fetch_meta")
    async def test_cache_invalidation(self, mock_meta, mock_pages, _key, defrost_page_sample):
        data = json.loads(defrost_page_sample)
        # Populate cache with old timestamp
        lookup_defrost._plates_cache = []
        lookup_defrost._plates_cache_updated = "2026-01-01T00:00:00Z"

        mock_meta.return_value = (
            {"rotation": 1, "numPages": 1, "updated": "2026-02-01T00:00:00Z"},
            None,
        )
        mock_pages.return_value = (data["records"], [])

        result = await _check_paginated_plates("TEST123")
        assert result.found is True
        mock_pages.assert_called_once()  # Should refetch

    @patch("lookup_defrost.get_decrypt_key", return_value="testkey")
    @patch("lookup_defrost.fetch_meta")
    async def test_meta_failure_with_stale_cache(self, mock_meta, _key, defrost_page_sample):
        data = json.loads(defrost_page_sample)
        lookup_defrost._plates_cache = data["records"]
        lookup_defrost._plates_cache_updated = "2026-01-01T00:00:00Z"

        mock_meta.return_value = (None, "Connection error")

        result = await _check_paginated_plates("TEST123")
        assert result.found is True  # Stale cache used

    @patch("lookup_defrost.get_decrypt_key", return_value="testkey")
    @patch("lookup_defrost.fetch_meta")
    async def test_meta_failure_no_cache(self, mock_meta, _key):
        mock_meta.return_value = (None, "Connection error")

        result = await _check_paginated_plates("TEST123")
        assert result.found is False
        assert "meta" in result.error

    @patch("lookup_defrost.get_decrypt_key", return_value="")
    async def test_no_decrypt_key(self, _key):
        result = await _check_paginated_plates("TEST123")
        assert result.found is False
        assert "DEFROST_DECRYPT_KEY" in result.error


# ---------------------------------------------------------------------------
# _check_stopice_fallback
# ---------------------------------------------------------------------------

class TestCheckStopiceFallback:
    @patch("lookup_defrost.get_defrost_url", return_value="https://example.com/plates.json")
    @patch("lookup_defrost.fetch_with_retry")
    async def test_cache_miss(self, mock_fetch, _url, defrost_json_sample):
        mock_fetch.return_value = (defrost_json_sample, None)
        result = await _check_stopice_fallback("TEST123")
        assert result.found is True
        assert result.record_count == 2

    @patch("lookup_defrost.get_defrost_url", return_value="https://example.com/plates.json")
    @patch("lookup_defrost.fetch_with_retry")
    async def test_cache_hit_within_ttl(self, mock_fetch, _url, defrost_json_sample):
        data = json.loads(defrost_json_sample)
        lookup_defrost._stopice_cache = data["plates"]
        lookup_defrost._stopice_cache_time = time.time()

        result = await _check_stopice_fallback("TEST123")
        assert result.found is True
        mock_fetch.assert_not_called()

    @patch("lookup_defrost.get_defrost_url", return_value="https://example.com/plates.json")
    @patch("lookup_defrost.fetch_with_retry")
    async def test_cache_expired(self, mock_fetch, _url, defrost_json_sample):
        data = json.loads(defrost_json_sample)
        lookup_defrost._stopice_cache = data["plates"]
        lookup_defrost._stopice_cache_time = time.time() - 4 * 3600  # 4 hours ago

        mock_fetch.return_value = (defrost_json_sample, None)
        result = await _check_stopice_fallback("TEST123")
        assert result.found is True
        mock_fetch.assert_called_once()

    @patch("lookup_defrost.get_defrost_url", return_value="")
    async def test_url_not_set(self, _url):
        result = await _check_stopice_fallback("TEST123")
        assert result.found is False
        assert "DEFROST_JSON_URL" in result.error

    @patch("lookup_defrost.get_defrost_url", return_value="https://example.com/plates.json")
    @patch("lookup_defrost.fetch_with_retry")
    async def test_fetch_failure_with_stale_cache(self, mock_fetch, _url, defrost_json_sample):
        data = json.loads(defrost_json_sample)
        lookup_defrost._stopice_cache = data["plates"]
        lookup_defrost._stopice_cache_time = time.time() - 4 * 3600  # expired

        mock_fetch.return_value = (None, "Connection error")
        result = await _check_stopice_fallback("TEST123")
        assert result.found is True  # Stale cache used

    @patch("lookup_defrost.get_defrost_url", return_value="https://example.com/plates.json")
    @patch("lookup_defrost.fetch_with_retry")
    async def test_fetch_failure_no_cache(self, mock_fetch, _url):
        mock_fetch.return_value = (None, "Connection error")
        result = await _check_stopice_fallback("TEST123")
        assert result.found is False
        assert result.error == "Connection error"

    @patch("lookup_defrost.get_defrost_url", return_value="https://example.com/plates.json")
    @patch("lookup_defrost.fetch_with_retry")
    async def test_invalid_json(self, mock_fetch, _url):
        mock_fetch.return_value = ("not json{{{", None)
        result = await _check_stopice_fallback("TEST123")
        assert result.found is False
        assert "Invalid JSON" in result.error

    @patch("lookup_defrost.get_defrost_url", return_value="https://example.com/plates.json")
    @patch("lookup_defrost.fetch_with_retry")
    async def test_invalid_json_with_stale_cache(self, mock_fetch, _url, defrost_json_sample):
        data = json.loads(defrost_json_sample)
        lookup_defrost._stopice_cache = data["plates"]
        lookup_defrost._stopice_cache_time = time.time() - 4 * 3600

        mock_fetch.return_value = ("not json{{{", None)
        result = await _check_stopice_fallback("TEST123")
        assert result.found is True  # Stale cache used


# ---------------------------------------------------------------------------
# _merge_results
# ---------------------------------------------------------------------------

class TestMergeResults:
    def test_both_found(self):
        r1 = LookupResult(found=True, match_count=1, record_count=3,
                          sightings=[Sighting(date="Jan 1", location="A")],
                          status="Confirmed ICE")
        r2 = LookupResult(found=True, match_count=1, record_count=2,
                          sightings=[Sighting(date="Feb 1", location="B")])
        merged = _merge_results(r1, r2)
        assert merged.found is True
        assert merged.match_count == 2
        assert merged.record_count == 5
        assert len(merged.sightings) == 2
        assert merged.status == "Confirmed ICE"

    def test_only_paginated_found(self):
        r1 = LookupResult(found=True, match_count=1, record_count=1,
                          sightings=[Sighting(date="Jan 1", location="A")])
        r2 = LookupResult(found=False)
        merged = _merge_results(r1, r2)
        assert merged.found is True
        assert merged.match_count == 1
        assert len(merged.sightings) == 1

    def test_only_stopice_found(self):
        r1 = LookupResult(found=False)
        r2 = LookupResult(found=True, match_count=1, record_count=2,
                          sightings=[Sighting(date="Jan 1", location="A")])
        merged = _merge_results(r1, r2)
        assert merged.found is True
        assert merged.match_count == 1

    def test_neither_found(self):
        r1 = LookupResult(found=False)
        r2 = LookupResult(found=False)
        merged = _merge_results(r1, r2)
        assert merged.found is False
        assert merged.error is None

    def test_neither_found_with_errors(self):
        r1 = LookupResult(found=False, error="paginated error")
        r2 = LookupResult(found=False, error="stopice error")
        merged = _merge_results(r1, r2)
        assert merged.found is False
        assert "paginated" in merged.error
        assert "stopice" in merged.error

    def test_one_found_one_error(self):
        r1 = LookupResult(found=True, match_count=1, record_count=1,
                          sightings=[Sighting(date="Jan 1", location="A")])
        r2 = LookupResult(found=False, error="stopice error")
        merged = _merge_results(r1, r2)
        assert merged.found is True
        assert merged.error is None  # Errors not included when found

    def test_duplicate_plate_both_sources_returns_all_sightings(self):
        """When the same plate exists in both sources, all sightings are included."""
        r1 = LookupResult(found=True, match_count=1, record_count=2,
                          sightings=[Sighting(date="Jan 1", location="Paginated loc")])
        r2 = LookupResult(found=True, match_count=1, record_count=3,
                          sightings=[
                              Sighting(date="Feb 1", location="Stopice loc A"),
                              Sighting(date="Mar 1", location="Stopice loc B"),
                          ])
        merged = _merge_results(r1, r2)
        assert merged.found is True
        assert merged.match_count == 2
        assert merged.record_count == 5
        assert len(merged.sightings) == 3
        # Paginated sightings come first, then stopice
        assert merged.sightings[0].location == "Paginated loc"
        assert merged.sightings[1].location == "Stopice loc A"
        assert merged.sightings[2].location == "Stopice loc B"

    def test_merge_propagates_paginated_status(self):
        r1 = LookupResult(found=True, match_count=1, record_count=1,
                          sightings=[Sighting(date="Jan 1", location="A")],
                          status="Confirmed ICE")
        r2 = LookupResult(found=True, match_count=1, record_count=1,
                          sightings=[Sighting(date="Feb 1", location="B")])
        merged = _merge_results(r1, r2)
        assert merged.status == "Confirmed ICE"

    def test_merge_falls_back_to_stopice_status(self):
        r1 = LookupResult(found=True, match_count=1, record_count=1,
                          sightings=[Sighting(date="Jan 1", location="A")])
        r2 = LookupResult(found=True, match_count=1, record_count=1,
                          sightings=[Sighting(date="Feb 1", location="B")],
                          status="Highly suspected ICE")
        merged = _merge_results(r1, r2)
        assert merged.status == "Highly suspected ICE"

    def test_merge_no_status(self):
        r1 = LookupResult(found=True, match_count=1, record_count=1,
                          sightings=[Sighting(date="Jan 1", location="A")])
        r2 = LookupResult(found=True, match_count=1, record_count=1,
                          sightings=[Sighting(date="Feb 1", location="B")])
        merged = _merge_results(r1, r2)
        assert merged.status is None


# ---------------------------------------------------------------------------
# check_plate_defrost (integration)
# ---------------------------------------------------------------------------

class TestCheckPlateDefrostIntegration:
    @patch("lookup_defrost._check_stopice_fallback")
    @patch("lookup_defrost._check_paginated_plates")
    async def test_both_sources_match(self, mock_paginated, mock_stopice):
        mock_paginated.return_value = LookupResult(
            found=True, match_count=1, record_count=3,
            sightings=[Sighting(date="Jan 1", location="A")],
        )
        mock_stopice.return_value = LookupResult(
            found=True, match_count=1, record_count=2,
            sightings=[Sighting(date="Feb 1", location="B")],
        )
        result = await check_plate_defrost("TEST123")
        assert result.found is True
        assert result.match_count == 2
        assert result.record_count == 5
        assert len(result.sightings) == 2

    @patch("lookup_defrost._check_stopice_fallback")
    @patch("lookup_defrost._check_paginated_plates")
    async def test_only_paginated_matches(self, mock_paginated, mock_stopice):
        mock_paginated.return_value = LookupResult(
            found=True, match_count=1, record_count=1,
            sightings=[Sighting(date="Jan 1", location="A")],
        )
        mock_stopice.return_value = LookupResult(found=False)
        result = await check_plate_defrost("TEST123")
        assert result.found is True

    @patch("lookup_defrost._check_stopice_fallback")
    @patch("lookup_defrost._check_paginated_plates")
    async def test_both_error(self, mock_paginated, mock_stopice):
        mock_paginated.return_value = LookupResult(found=False, error="decrypt error")
        mock_stopice.return_value = LookupResult(found=False, error="fetch error")
        result = await check_plate_defrost("TEST123")
        assert result.found is False
        assert result.error is not None

    @patch("lookup_defrost._check_stopice_fallback")
    @patch("lookup_defrost._check_paginated_plates")
    async def test_neither_matches_no_error(self, mock_paginated, mock_stopice):
        mock_paginated.return_value = LookupResult(found=False)
        mock_stopice.return_value = LookupResult(found=False)
        result = await check_plate_defrost("ZZZZZZZ")
        assert result.found is False
        assert result.error is None


# ---------------------------------------------------------------------------
# _get_cache_dir
# ---------------------------------------------------------------------------

class TestGetCacheDir:
    def test_returns_env_var(self):
        with patch.dict(os.environ, {"CACHE_DIR": "/tmp/test-cache"}):
            assert _get_cache_dir() == "/tmp/test-cache"

    def test_returns_empty_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _get_cache_dir() == ""

    def test_returns_empty_string_value(self):
        with patch.dict(os.environ, {"CACHE_DIR": ""}):
            assert _get_cache_dir() == ""


# ---------------------------------------------------------------------------
# _save_cache / _load_cache
# ---------------------------------------------------------------------------

class TestSaveLoadCache:
    def test_save_and_load_roundtrip(self, tmp_path):
        data = {"key": "value", "numbers": [1, 2, 3]}
        with patch.dict(os.environ, {"CACHE_DIR": str(tmp_path)}):
            _save_cache("test.json", data)
            loaded = _load_cache("test.json")
        assert loaded == data

    def test_save_creates_directory(self, tmp_path):
        cache_dir = str(tmp_path / "subdir" / "cache")
        data = {"test": True}
        with patch.dict(os.environ, {"CACHE_DIR": cache_dir}):
            _save_cache("test.json", data)
            assert os.path.exists(os.path.join(cache_dir, "test.json"))

    def test_save_noop_without_cache_dir(self, tmp_path):
        with patch.dict(os.environ, {}, clear=True):
            _save_cache("test.json", {"test": True})
        assert not (tmp_path / "test.json").exists()

    def test_load_returns_none_without_cache_dir(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _load_cache("test.json") is None

    def test_load_returns_none_for_missing_file(self, tmp_path):
        with patch.dict(os.environ, {"CACHE_DIR": str(tmp_path)}):
            assert _load_cache("nonexistent.json") is None

    def test_load_returns_none_for_corrupt_json(self, tmp_path):
        (tmp_path / "bad.json").write_text("not valid json{{{")
        with patch.dict(os.environ, {"CACHE_DIR": str(tmp_path)}):
            assert _load_cache("bad.json") is None

    def test_save_atomic_no_leftover_tmp(self, tmp_path):
        data = {"key": "value"}
        with patch.dict(os.environ, {"CACHE_DIR": str(tmp_path)}):
            _save_cache("test.json", data)
        assert not (tmp_path / "test.json.tmp").exists()
        assert (tmp_path / "test.json").exists()

    def test_save_overwrites_existing(self, tmp_path):
        with patch.dict(os.environ, {"CACHE_DIR": str(tmp_path)}):
            _save_cache("test.json", {"version": 1})
            _save_cache("test.json", {"version": 2})
            loaded = _load_cache("test.json")
        assert loaded == {"version": 2}


# ---------------------------------------------------------------------------
# Disk cache integration: _check_paginated_plates
# ---------------------------------------------------------------------------

class TestPaginatedDiskCache:
    @patch("lookup_defrost.get_decrypt_key", return_value="testkey")
    @patch("lookup_defrost.fetch_all_pages")
    @patch("lookup_defrost.fetch_meta")
    async def test_saves_to_disk_after_fetch(self, mock_meta, mock_pages, _key,
                                              tmp_path, defrost_page_sample):
        data = json.loads(defrost_page_sample)
        mock_meta.return_value = (
            {"rotation": 1, "numPages": 1, "updated": "2026-02-01T00:00:00Z"},
            None,
        )
        mock_pages.return_value = (data["records"], [])

        with patch.dict(os.environ, {"CACHE_DIR": str(tmp_path)}):
            result = await _check_paginated_plates("TEST123")
        assert result.found is True
        cache_file = tmp_path / "cache_paginated.json"
        assert cache_file.exists()
        cached = json.loads(cache_file.read_text())
        assert cached["updated"] == "2026-02-01T00:00:00Z"
        assert len(cached["records"]) == len(data["records"])

    @patch("lookup_defrost.get_decrypt_key", return_value="testkey")
    @patch("lookup_defrost.fetch_meta")
    async def test_loads_from_disk_on_cold_start(self, mock_meta, _key,
                                                  tmp_path, defrost_page_sample):
        data = json.loads(defrost_page_sample)
        # Pre-populate disk cache
        cache_data = {"updated": "2026-02-01T00:00:00Z", "records": data["records"]}
        (tmp_path / "cache_paginated.json").write_text(json.dumps(cache_data))

        # Meta returns same timestamp → cache hit, no page fetch needed
        mock_meta.return_value = (
            {"rotation": 1, "numPages": 1, "updated": "2026-02-01T00:00:00Z"},
            None,
        )

        with patch.dict(os.environ, {"CACHE_DIR": str(tmp_path)}):
            result = await _check_paginated_plates("TEST123")
        assert result.found is True

    @patch("lookup_defrost.get_decrypt_key", return_value="testkey")
    @patch("lookup_defrost.fetch_meta")
    async def test_disk_load_serves_stale_on_meta_failure(self, mock_meta, _key,
                                                           tmp_path, defrost_page_sample):
        data = json.loads(defrost_page_sample)
        cache_data = {"updated": "2026-01-01T00:00:00Z", "records": data["records"]}
        (tmp_path / "cache_paginated.json").write_text(json.dumps(cache_data))

        mock_meta.return_value = (None, "Connection error")

        with patch.dict(os.environ, {"CACHE_DIR": str(tmp_path)}):
            result = await _check_paginated_plates("TEST123")
        assert result.found is True  # Stale disk cache used


# ---------------------------------------------------------------------------
# Disk cache integration: _check_stopice_fallback
# ---------------------------------------------------------------------------

class TestStopiceDiskCache:
    @patch("lookup_defrost.get_defrost_url", return_value="https://example.com/plates.json")
    @patch("lookup_defrost.fetch_with_retry")
    async def test_saves_to_disk_after_fetch(self, mock_fetch, _url,
                                              tmp_path, defrost_json_sample):
        mock_fetch.return_value = (defrost_json_sample, None)

        with patch.dict(os.environ, {"CACHE_DIR": str(tmp_path)}):
            result = await _check_stopice_fallback("TEST123")
        assert result.found is True
        cache_file = tmp_path / "cache_stopice.json"
        assert cache_file.exists()
        cached = json.loads(cache_file.read_text())
        assert "cache_time" in cached
        assert "plates" in cached

    @patch("lookup_defrost.get_defrost_url", return_value="https://example.com/plates.json")
    @patch("lookup_defrost.fetch_with_retry")
    async def test_loads_from_disk_within_ttl(self, mock_fetch, _url,
                                               tmp_path, defrost_json_sample):
        data = json.loads(defrost_json_sample)
        cache_data = {"cache_time": time.time(), "plates": data["plates"]}
        (tmp_path / "cache_stopice.json").write_text(json.dumps(cache_data))

        with patch.dict(os.environ, {"CACHE_DIR": str(tmp_path)}):
            result = await _check_stopice_fallback("TEST123")
        assert result.found is True
        mock_fetch.assert_not_called()  # Served from disk cache

    @patch("lookup_defrost.get_defrost_url", return_value="https://example.com/plates.json")
    @patch("lookup_defrost.fetch_with_retry")
    async def test_disk_load_expired_refetches(self, mock_fetch, _url,
                                                tmp_path, defrost_json_sample):
        data = json.loads(defrost_json_sample)
        cache_data = {"cache_time": time.time() - 4 * 3600, "plates": data["plates"]}
        (tmp_path / "cache_stopice.json").write_text(json.dumps(cache_data))

        mock_fetch.return_value = (defrost_json_sample, None)

        with patch.dict(os.environ, {"CACHE_DIR": str(tmp_path)}):
            result = await _check_stopice_fallback("TEST123")
        assert result.found is True
        mock_fetch.assert_called_once()  # Expired, so refetched

    @patch("lookup_defrost.get_defrost_url", return_value="https://example.com/plates.json")
    @patch("lookup_defrost.fetch_with_retry")
    async def test_disk_load_serves_stale_on_fetch_failure(self, mock_fetch, _url,
                                                            tmp_path, defrost_json_sample):
        data = json.loads(defrost_json_sample)
        cache_data = {"cache_time": time.time() - 4 * 3600, "plates": data["plates"]}
        (tmp_path / "cache_stopice.json").write_text(json.dumps(cache_data))

        mock_fetch.return_value = (None, "Connection error")

        with patch.dict(os.environ, {"CACHE_DIR": str(tmp_path)}):
            result = await _check_stopice_fallback("TEST123")
        assert result.found is True  # Stale disk cache used

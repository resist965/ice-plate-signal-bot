"""Tests for async HTTP functions in lookup.py."""

import re
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aioresponses import aioresponses

import lookup
from lookup import (
    BASE_URL,
    LookupResult,
    fetch_with_retry,
    _get_session,
    check_plate,
    close_session,
    fetch_descriptions,
)

# Pattern to match BASE_URL with any query params
BASE_URL_PATTERN = re.compile(re.escape(BASE_URL) + r"(\?.*)?$")


@pytest.fixture
def mock_aio():
    with aioresponses() as m:
        yield m


# ---------------------------------------------------------------------------
# fetch_with_retry
# ---------------------------------------------------------------------------

class TestFetchWithRetry:
    async def test_200_returns_html(self, mock_aio):
        mock_aio.post(BASE_URL, status=200, body="<html>ok</html>")
        html, err = await fetch_with_retry("POST", BASE_URL, data={"search": "1"})
        assert html == "<html>ok</html>"
        assert err is None

    async def test_404_returns_error_no_retry(self, mock_aio):
        mock_aio.post(BASE_URL, status=404)
        html, err = await fetch_with_retry("POST", BASE_URL)
        assert html is None
        assert err == "Lookup service unavailable"

    @patch("lookup.asyncio.sleep", new_callable=AsyncMock)
    async def test_500_all_attempts_returns_error(self, _mock_sleep, mock_aio):
        for _ in range(3):
            mock_aio.post(BASE_URL, status=500)
        html, err = await fetch_with_retry("POST", BASE_URL)
        assert html is None
        assert err == "Could not reach lookup service"

    @patch("lookup.asyncio.sleep", new_callable=AsyncMock)
    async def test_500_then_200_succeeds(self, _mock_sleep, mock_aio):
        mock_aio.post(BASE_URL, status=500)
        mock_aio.post(BASE_URL, status=200, body="<html>retry ok</html>")
        html, err = await fetch_with_retry("POST", BASE_URL)
        assert html == "<html>retry ok</html>"
        assert err is None

    @patch("lookup.asyncio.sleep", new_callable=AsyncMock)
    async def test_client_error_all_attempts(self, _mock_sleep, mock_aio):
        for _ in range(3):
            mock_aio.post(BASE_URL, exception=aiohttp.ClientError("connection failed"))
        html, err = await fetch_with_retry("POST", BASE_URL)
        assert html is None
        assert err == "Could not reach lookup service"

    async def test_generic_exception_returns_error(self, mock_aio):
        mock_aio.post(BASE_URL, exception=RuntimeError("unexpected"))
        html, err = await fetch_with_retry("POST", BASE_URL)
        assert html is None
        assert err == "Unexpected error during lookup"


# ---------------------------------------------------------------------------
# check_plate
# ---------------------------------------------------------------------------

class TestCheckPlate:
    async def test_match_found(self, mock_aio, html_search_match):
        mock_aio.post(BASE_URL, status=200, body=html_search_match)
        result = await check_plate("SXF180")
        assert result.found is True
        assert result.match_count == 1
        assert result.record_count == 3  # 1 shown + "2 more records"
        assert len(result.sightings) == 1

    async def test_no_match(self, mock_aio, html_search_no_match):
        mock_aio.post(BASE_URL, status=200, body=html_search_no_match)
        result = await check_plate("ZZZZ000")
        assert result.found is False
        assert result.sightings == []

    async def test_http_failure(self, mock_aio):
        mock_aio.post(BASE_URL, status=404)
        result = await check_plate("ABC123")
        assert result.found is False
        assert result.error is not None

    async def test_post_is_called(self, mock_aio, html_search_no_match):
        mock_aio.post(BASE_URL, status=200, body=html_search_no_match)
        await check_plate("XYZ789")
        # Verify a POST was made (aioresponses tracks requests)
        all_keys = list(mock_aio.requests.keys())
        post_keys = [k for k in all_keys if k[0] == "POST"]
        assert len(post_keys) == 1


# ---------------------------------------------------------------------------
# fetch_descriptions
# ---------------------------------------------------------------------------

class TestFetchDescriptions:
    async def test_success(self, mock_aio, html_detail_page):
        mock_aio.get(BASE_URL_PATTERN, status=200, body=html_detail_page)
        result = await fetch_descriptions("SXF180")
        assert result.found is True
        assert len(result.sightings) >= 1

    async def test_no_sightings(self, mock_aio):
        mock_aio.get(BASE_URL_PATTERN, status=200, body="<html></html>")
        result = await fetch_descriptions("SXF180")
        assert result.found is False

    async def test_http_failure(self, mock_aio):
        mock_aio.get(BASE_URL_PATTERN, status=404)
        result = await fetch_descriptions("ABC123")
        assert result.found is False
        assert result.error is not None

    async def test_get_is_called(self, mock_aio):
        mock_aio.get(BASE_URL_PATTERN, status=200, body="<html></html>")
        await fetch_descriptions("XYZ789")
        all_keys = list(mock_aio.requests.keys())
        get_keys = [k for k in all_keys if k[0] == "GET"]
        assert len(get_keys) == 1


# ---------------------------------------------------------------------------
# _get_session / close_session
# ---------------------------------------------------------------------------

class TestSessionManagement:
    async def test_get_session_creates_session(self):
        session = _get_session()
        assert session is not None
        assert isinstance(session, aiohttp.ClientSession)

    async def test_get_session_returns_same(self):
        s1 = _get_session()
        s2 = _get_session()
        assert s1 is s2

    async def test_close_session_sets_none(self):
        _get_session()
        await close_session()
        assert lookup._session is None

    async def test_close_session_when_none(self):
        lookup._session = None
        await close_session()  # should not raise
        assert lookup._session is None

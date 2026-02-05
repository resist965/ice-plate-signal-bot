"""License plate lookup against stopice.net database."""

import asyncio
import logging
import re
from dataclasses import dataclass, field

import aiohttp
from bs4 import BeautifulSoup

BASE_URL = "https://www.stopice.net/platetracker/index.cgi"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible)"}
TIMEOUT = aiohttp.ClientTimeout(total=15)

logger = logging.getLogger(__name__)

_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    """Return a reusable aiohttp session, creating it lazily."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(headers=HEADERS, timeout=TIMEOUT)
    return _session


async def close_session() -> None:
    """Close the shared aiohttp session. Call on shutdown."""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


@dataclass
class Sighting:
    date: str
    location: str
    vehicle: str = ""
    description: str = ""
    time: str = ""


@dataclass
class LookupResult:
    found: bool
    match_count: int = 0
    record_count: int = 0
    sightings: list[Sighting] = field(default_factory=list)
    error: str | None = None
    status: str | None = None


async def fetch_with_retry(method: str, url: str, **kwargs) -> tuple[str | None, str | None]:
    """Perform an HTTP request with retries.

    Returns (html, None) on success or (None, error_msg) on failure.
    Retries on ClientError, TimeoutError, and 5xx responses (3 attempts, 2s backoff).
    Returns immediately with an error on 4xx responses.
    """
    session = _get_session()
    for attempt in range(3):
        try:
            async with session.request(method, url, **kwargs) as resp:
                if resp.status >= 500:
                    logger.warning("Server error %d (attempt %d/3)", resp.status, attempt + 1)
                    if attempt < 2:
                        await asyncio.sleep(2)
                    continue
                if resp.status != 200:
                    logger.warning("HTTP %d for %s %s", resp.status, method, url)
                    return None, "Lookup service unavailable"
                html = await resp.text()
            return html, None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.warning("Attempt %d/3 failed for %s %s", attempt + 1, method, url)
            if attempt < 2:
                await asyncio.sleep(2)
        except Exception:
            logger.exception("Unexpected error for %s %s", method, url)
            return None, "Unexpected error during lookup"

    return None, "Could not reach lookup service"


async def check_plate(plate: str) -> LookupResult:
    """Check a license plate against the stopice.net database.

    Returns a LookupResult with match info and sighting details.
    """
    html, error = await fetch_with_retry(
        "POST", BASE_URL, data={"search": "1", "keywords": plate},
    )
    if error:
        return LookupResult(found=False, error=error)

    result_match = re.search(r"<!--RESULT:(\d+)-->", html)
    if not result_match or result_match.group(1) == "0":
        return LookupResult(found=False)

    match_count = int(result_match.group(1))
    sightings = _parse_search_results_from_html(html)
    record_count = _extract_record_count(html, len(sightings))

    return LookupResult(
        found=True,
        match_count=match_count,
        record_count=record_count,
        sightings=sightings,
    )


async def fetch_descriptions(plate: str) -> LookupResult:
    """Fetch the detail page for a plate and return a LookupResult with full sighting records."""
    html, error = await fetch_with_retry(
        "GET", BASE_URL, params={"plate": plate},
    )
    if error:
        return LookupResult(found=False, error=error)

    sightings = _parse_detail_page(html)
    return LookupResult(found=bool(sightings), sightings=sightings)


def _parse_search_results_from_html(html: str) -> list[Sighting]:
    """Parse sightings from the search results page using regex.

    The HTML from stopice.net is heavily malformed, so regex on the raw HTML
    is more reliable than DOM traversal. Each result block contains:
    - Date in <font style=font-size:9pt; color=#c0c0c0>DATE
    - Location after <img src=mapmarker.png ...> LOCATION
    - Description in the last <font style=font-size:9pt;> block
    """
    sightings = []

    # Split HTML at each date marker to isolate result blocks
    # Pattern: <font style=font-size:9pt; color=#c0c0c0> (with varying whitespace/quotes)
    blocks = re.split(
        r'<font\s+style=["\']?font-size:9pt;?["\']?\s+color=["\']?#c0c0c0["\']?\s*>',
        html,
        flags=re.IGNORECASE,
    )

    for block in blocks[1:]:  # skip everything before first date
        # Stop at the RESULT comment or end-of-results marker
        block = block.split("<!--RESULT:")[0]

        # Date: first line of text content
        date_match = re.match(r"\s*([^<\n]+)", block)
        date_text = date_match.group(1).strip() if date_match else ""

        # Location: text after mapmarker img
        location = ""
        loc_match = re.search(
            r'<img\s+src=["\']?mapmarker\.png["\']?[^>]*>\s*(.+?)(?:<|\n)',
            block,
            re.IGNORECASE,
        )
        if loc_match:
            location = loc_match.group(1).strip()

        # Description: content of the last <font style=font-size:9pt;> block
        # that isn't the location or record count
        description = ""
        desc_candidates = re.findall(
            r'<font\s+style=["\']?font-size:9pt;?["\']?\s*>\s*([^<\n]+)',
            block,
            re.IGNORECASE,
        )
        for candidate in desc_candidates:
            text = candidate.strip()
            if (
                text
                and "more records" not in text.lower()
                and "mapmarker" not in text.lower()
                and text != location
            ):
                description = text

        if date_text:
            sightings.append(Sighting(
                date=date_text,
                location=location,
                description=description,
            ))

    return sightings


def _extract_record_count(html: str, shown: int) -> int:
    """Compute the total record count.

    If the page says "N more records", the total is N + shown (since
    'N more' means N beyond the ones already displayed).
    Otherwise, the shown count is the total.
    """
    m = re.search(r"(\d+)\s+more records", html, re.IGNORECASE)
    if m:
        return int(m.group(1)) + shown
    return shown


def _parse_detail_page(html: str) -> list[Sighting]:
    """Parse the detail page to extract all sighting records with full descriptions.

    Each record block has:
    - Date: <font style="font-size:18pt;" color="#555"><b>DATE</b>
    - Location: <font color="red">LOCATION (filter out × close buttons)
    - Vehicle: in a Table cell before the "created:"/"added:" timestamp
    - Description: <font style="font-size:14pt;">TEXT
    """
    soup = BeautifulSoup(html, "html.parser")
    sightings = []

    # Dates: font with 18pt and color #555
    date_fonts = soup.find_all(
        "font", style=re.compile(r"font-size:18pt"), color="#555"
    )

    # Locations: font color=red, excluding close-button × characters
    location_fonts = [
        f for f in soup.find_all("font", color="red")
        if f.get_text(strip=True) not in ("×", "")
    ]

    # Descriptions: font 14pt — skip non-description entries (modals etc.)
    # The description fonts appear after the date/location blocks in document order.
    # Filter to those that contain actual descriptive text (not UI chrome).
    desc_fonts = []
    for f in soup.find_all("font", style=re.compile(r"font-size:14pt")):
        text = f.get_text(strip=True)
        if text and "upcoming action" not in text.lower() and text != "UNCONFIRMED":
            desc_fonts.append(f)

    # Vehicle and time: extracted from Table cells around "created:"/"added:" timestamps
    vehicle_texts = []
    time_texts = []
    for f in soup.find_all("font", style=re.compile(r"font-size:9pt")):
        text = f.get_text(strip=True)
        if text.startswith(("created:", "added:")):
            # Use only the font element's own direct text to avoid
            # picking up child elements like "2 records [update]".
            direct_text = f.find(string=True, recursive=False)
            direct_text = direct_text.strip() if direct_text else ""
            if direct_text.startswith("created:"):
                time_texts.append(direct_text[len("created:"):].strip())
            elif direct_text.startswith("added:"):
                time_texts.append(direct_text[len("added:"):].strip())
            else:
                time_texts.append("")
            parent_table = f.find_parent("table", cellpadding="0")
            if parent_table:
                prev = parent_table.find_previous_sibling("table", cellpadding="0")
                if prev:
                    vehicle_texts.append(prev.get_text(strip=True))
                else:
                    vehicle_texts.append("")
            else:
                vehicle_texts.append("")

    for i, date_font in enumerate(date_fonts):
        date_text = date_font.get_text(strip=True)
        location = location_fonts[i].get_text(strip=True) if i < len(location_fonts) else ""
        description = desc_fonts[i].get_text(strip=True) if i < len(desc_fonts) else ""
        vehicle = vehicle_texts[i] if i < len(vehicle_texts) else ""

        sightings.append(Sighting(
            date=date_text,
            location=location,
            vehicle=vehicle,
            description=description,
            time=time_texts[i] if i < len(time_texts) else "",
        ))

    return sightings

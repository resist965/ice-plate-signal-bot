"""License plate lookup against defrostmn.net databases.

Searches two sources and merges results:
1. Paginated encrypted plates (defrostmn's own data) — cached until metadata timestamp changes
2. Legacy stopice_plates.json (stopice snapshot) — cached for 3 hours
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from datetime import datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from lookup import LookupResult, Sighting, fetch_with_retry

logger = logging.getLogger(__name__)

_DEFROST_DATA_BASE = "https://defrostmn.net/data/plates"
_STOPICE_CACHE_TTL = 3 * 3600  # 3 hours
_MAX_CONCURRENT_PAGES = 10

# Module-level caches
_plates_cache: list[dict] | None = None
_plates_cache_updated: str | None = None

_stopice_cache: list[dict] | None = None
_stopice_cache_time: float | None = None

_PAGINATED_CACHE_FILE = "cache_paginated.json"
_STOPICE_CACHE_FILE = "cache_stopice.json"


def _get_cache_dir() -> str:
    """Return cache directory from CACHE_DIR env var, or empty to disable."""
    return os.environ.get("CACHE_DIR", "")


def _save_cache(filename: str, data: dict) -> None:
    """Write data as JSON to CACHE_DIR/filename atomically (temp + rename)."""
    cache_dir = _get_cache_dir()
    if not cache_dir:
        return
    try:
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, filename)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
    except OSError as e:
        logger.warning("Failed to save cache %s: %s", filename, e)


def _load_cache(filename: str) -> dict | None:
    """Load JSON from CACHE_DIR/filename, returning None on any failure."""
    cache_dir = _get_cache_dir()
    if not cache_dir:
        return None
    try:
        path = os.path.join(cache_dir, filename)
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return None


def clear_caches() -> None:
    """Reset all module-level cache state (for tests)."""
    global _plates_cache, _plates_cache_updated
    global _stopice_cache, _stopice_cache_time
    _plates_cache = None
    _plates_cache_updated = None
    _stopice_cache = None
    _stopice_cache_time = None


def get_defrost_url() -> str:
    """Return the defrost stopice JSON URL from the environment."""
    return os.environ.get("DEFROST_JSON_URL", "")


def get_decrypt_key() -> str:
    """Return the decryption key from the environment."""
    return os.environ.get("DEFROST_DECRYPT_KEY", "")


def _format_date(record: dict) -> str:
    """Format a date string from month/day/year fields, falling back to datestamp."""
    month = record.get("month", "")
    day = record.get("day", "")
    year = record.get("year", "")
    if month and day and year:
        return f"{month} {day}, {year}"
    return record.get("datestamp", "")


def _decrypt_page(encrypted: dict, password: str) -> str:
    """Decrypt an AES-256-GCM encrypted page.

    Args:
        encrypted: dict with base64-encoded 'salt', 'iv', and 'ciphertext' fields
        password: the decryption passphrase

    Returns:
        The decrypted plaintext as a string.
    """
    salt = base64.b64decode(encrypted["salt"])
    iv = base64.b64decode(encrypted["iv"])
    ciphertext = base64.b64decode(encrypted["ciphertext"])

    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000, dklen=32)
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ciphertext, None)
    return plaintext.decode("utf-8")


def _format_iso_date(iso_str: str) -> str:
    """Format an ISO 8601 timestamp to a readable date string."""
    if not iso_str:
        return ""
    try:
        # Parse "2026-01-27T19:30:00.000Z" → "Jan 27, 2026"
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return iso_str


def _record_to_sighting(fields: dict) -> Sighting:
    """Convert a paginated plate record's fields to a Sighting."""
    last_seen = fields.get("Last Seen", "")
    location = fields.get("Last Location Seen", "")
    vehicle = fields.get("Vehicle Description", "") or fields.get("Unique vehicles", "")
    status = fields.get("Plate Status", [])
    tags = fields.get("Tags", "")

    # Build description from status and tags
    desc_parts = []
    if status:
        desc_parts.append(" / ".join(status))
    if tags:
        desc_parts.append(tags)
    description = " | ".join(desc_parts) if desc_parts else ""

    return Sighting(
        date=_format_iso_date(last_seen),
        location=location,
        vehicle=vehicle,
        description=description,
    )


async def fetch_meta() -> tuple[dict | None, str | None]:
    """Fetch Plates_meta.json from defrostmn.net.

    Returns:
        (meta_dict, None) on success or (None, error_msg) on failure.
    """
    url = f"{_DEFROST_DATA_BASE}/Plates_meta.json"
    body, error = await fetch_with_retry("GET", url)
    if error:
        return None, error
    try:
        return json.loads(body), None
    except (ValueError, TypeError):
        return None, "Invalid meta JSON from defrostmn.net"


async def fetch_all_pages(rotation: int, num_pages: int) -> tuple[list[dict], list[str]]:
    """Fetch all encrypted pages concurrently and decrypt them.

    Returns:
        (combined_records, errors) where combined_records is a list of all
        plate record dicts and errors is a list of error messages for any
        pages that failed.
    """
    password = get_decrypt_key()
    if not password:
        return [], ["DEFROST_DECRYPT_KEY not configured"]

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_PAGES)

    async def fetch_page(page_num: int) -> tuple[list[dict], str | None]:
        async with semaphore:
            url = f"{_DEFROST_DATA_BASE}/Plates_r{rotation}_p{page_num}.json"
            body, error = await fetch_with_retry("GET", url)
            if error:
                return [], f"Page {page_num}: {error}"
            try:
                encrypted = json.loads(body)
                plaintext = _decrypt_page(encrypted, password)
                data = json.loads(plaintext)
                return data.get("records", []), None
            except Exception as e:
                logger.warning("Failed to decrypt page %d: %s", page_num, e)
                return [], f"Page {page_num}: decryption failed"

    tasks = [fetch_page(i) for i in range(1, num_pages + 1)]
    results = await asyncio.gather(*tasks)

    all_records = []
    errors = []
    for records, err in results:
        all_records.extend(records)
        if err:
            errors.append(err)

    return all_records, errors


def _search_paginated_plates(plates_list: list[dict], plate: str) -> LookupResult:
    """Search paginated plate records for an exact plate match."""
    plate_upper = plate.upper()

    for entry in plates_list:
        fields = entry.get("fields", {})
        entry_plate = fields.get("Plate", "")
        if entry_plate.upper() == plate_upper:
            sighting = _record_to_sighting(fields)
            plate_status = fields.get("Plate Status", [])
            status_str = " / ".join(plate_status) if plate_status else None
            return LookupResult(
                found=True,
                match_count=1,
                record_count=fields.get("Reports Count", 1),
                sightings=[sighting],
                status=status_str,
            )

    return LookupResult(found=False)


def _search_stopice_plates(plates_list: list[dict], plate: str) -> LookupResult:
    """Search stopice snapshot plates for an exact plate match."""
    plate_upper = plate.upper()

    for entry in plates_list:
        if entry.get("license_plate", "").upper() == plate_upper:
            records = entry.get("records", [])
            sightings = []
            for rec in records:
                sightings.append(Sighting(
                    date=_format_date(rec),
                    location=rec.get("address", ""),
                    vehicle=rec.get("vehicle_make", ""),
                    description=rec.get("comments", ""),
                    time=rec.get("datestamp", ""),
                ))
            return LookupResult(
                found=True,
                match_count=1,
                record_count=len(sightings),
                sightings=sightings,
            )

    return LookupResult(found=False)


async def _check_paginated_plates(plate: str) -> LookupResult:
    """Fetch/cache/search paginated encrypted plate data.

    Fetches metadata first (lightweight) to check if data has changed.
    Only refetches all pages if the updated timestamp has changed.
    Falls back to stale cache if meta fetch fails.
    """
    global _plates_cache, _plates_cache_updated

    password = get_decrypt_key()
    if not password:
        return LookupResult(found=False, error="DEFROST_DECRYPT_KEY not configured")

    # Load from disk if in-memory cache is empty
    if _plates_cache is None:
        disk = _load_cache(_PAGINATED_CACHE_FILE)
        if disk and "records" in disk and "updated" in disk:
            _plates_cache = disk["records"]
            _plates_cache_updated = disk["updated"]
            logger.info("Loaded paginated plates cache from disk")

    meta, meta_error = await fetch_meta()

    if meta_error:
        logger.warning("Meta fetch failed: %s", meta_error)
        if _plates_cache is not None:
            logger.info("Using stale paginated plates cache")
            return _search_paginated_plates(_plates_cache, plate)
        return LookupResult(found=False, error=f"defrostmn.net meta: {meta_error}")

    updated = meta.get("updated", "")
    if _plates_cache is not None and updated == _plates_cache_updated:
        return _search_paginated_plates(_plates_cache, plate)

    rotation = meta.get("rotation", 1)
    num_pages = meta.get("numPages", 1)

    records, errors = await fetch_all_pages(rotation, num_pages)

    if records:
        _plates_cache = records
        _plates_cache_updated = updated
        _save_cache(_PAGINATED_CACHE_FILE, {"updated": updated, "records": records})
    elif _plates_cache is not None:
        logger.warning("All pages failed, using stale cache. Errors: %s", errors)
        return _search_paginated_plates(_plates_cache, plate)
    else:
        error_summary = "; ".join(errors[:3])
        return LookupResult(found=False, error=f"defrostmn.net pages: {error_summary}")

    return _search_paginated_plates(_plates_cache, plate)


async def _check_stopice_fallback(plate: str) -> LookupResult:
    """Fetch/cache/search the stopice snapshot JSON.

    Uses a 3-hour TTL cache. Falls back to stale cache if fetch fails.
    """
    global _stopice_cache, _stopice_cache_time

    url = get_defrost_url()
    if not url:
        return LookupResult(found=False, error="DEFROST_JSON_URL not configured")

    # Load from disk if in-memory cache is empty
    if _stopice_cache is None:
        disk = _load_cache(_STOPICE_CACHE_FILE)
        if disk and "plates" in disk and "cache_time" in disk:
            _stopice_cache = disk["plates"]
            _stopice_cache_time = disk["cache_time"]
            logger.info("Loaded stopice cache from disk")

    now = time.time()
    if _stopice_cache is not None and _stopice_cache_time is not None:
        if now - _stopice_cache_time < _STOPICE_CACHE_TTL:
            return _search_stopice_plates(_stopice_cache, plate)

    body, error = await fetch_with_retry("GET", url)
    if error:
        if _stopice_cache is not None:
            logger.warning("Stopice fetch failed (%s), using stale cache", error)
            return _search_stopice_plates(_stopice_cache, plate)
        return LookupResult(found=False, error=error)

    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        if _stopice_cache is not None:
            logger.warning("Invalid stopice JSON, using stale cache")
            return _search_stopice_plates(_stopice_cache, plate)
        return LookupResult(found=False, error="Invalid JSON from defrostmn.net")

    _stopice_cache = data.get("plates", [])
    _stopice_cache_time = now
    _save_cache(_STOPICE_CACHE_FILE, {"cache_time": now, "plates": _stopice_cache})

    return _search_stopice_plates(_stopice_cache, plate)


def _merge_results(paginated: LookupResult, stopice: LookupResult) -> LookupResult:
    """Combine results from both defrost sources into a single LookupResult."""
    # Collect errors from sources that failed
    errors = []
    if paginated.error:
        errors.append(f"paginated: {paginated.error}")
    if stopice.error:
        errors.append(f"stopice: {stopice.error}")

    # If neither found anything
    if not paginated.found and not stopice.found:
        error_str = "; ".join(errors) if errors else None
        return LookupResult(found=False, error=error_str)

    # Merge sightings — paginated results first, then stopice
    all_sightings = []
    total_match = 0
    total_records = 0

    if paginated.found:
        all_sightings.extend(paginated.sightings)
        total_match += paginated.match_count
        total_records += paginated.record_count

    if stopice.found:
        all_sightings.extend(stopice.sightings)
        total_match += stopice.match_count
        total_records += stopice.record_count

    status = paginated.status or stopice.status

    return LookupResult(
        found=True,
        match_count=total_match,
        record_count=total_records,
        sightings=all_sightings,
        status=status,
    )


async def check_plate_defrost(plate: str) -> LookupResult:
    """Check a license plate against both defrostmn.net sources.

    Queries paginated encrypted plates and the legacy stopice snapshot
    concurrently, then merges results.
    """
    paginated_result, stopice_result = await asyncio.gather(
        _check_paginated_plates(plate),
        _check_stopice_fallback(plate),
    )

    return _merge_results(paginated_result, stopice_result)

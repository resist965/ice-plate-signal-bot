"""Health-check script for live data sources.

Makes live requests to stopice.net and defrostmn.net, exercises existing
parsing/decryption code, validates structural integrity, and prints a
clear pass/fail summary.
"""

import argparse
import asyncio
import json
import logging
import os
import sys

from lookup import LookupResult, check_plate, close_session, fetch_descriptions
from lookup_defrost import (
    check_plate_defrost,
    clear_caches,
    fetch_all_pages,
    fetch_meta,
    fetch_with_retry,
    get_decrypt_key,
    get_defrost_url,
)

# Terminal colors
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _pass(label: str, detail: str = "") -> bool:
    print(f"  {GREEN}[PASS]{RESET} {label}")
    if detail:
        print(f"         {detail}")
    return True


def _fail(label: str, detail: str = "") -> bool:
    print(f"  {RED}[FAIL]{RESET} {label}")
    if detail:
        print(f"         {detail}")
    return False


def _skip(label: str, detail: str = "") -> None:
    print(f"  {YELLOW}[SKIP]{RESET} {label}")
    if detail:
        print(f"         {detail}")


async def check_stopice_search(plate: str) -> bool:
    """Check 1: stopice.net search page."""
    label = "stopice.net search"
    result: LookupResult = await check_plate(plate)
    if result.error:
        return _fail(label, f"Error: {result.error}")
    if not result.found:
        return _fail(label, f"Plate {plate!r} not found (use a known plate)")
    if result.match_count < 1:
        return _fail(label, f"match_count={result.match_count}, expected >= 1")
    if not result.sightings:
        return _fail(label, "No sightings parsed from search results")
    for i, s in enumerate(result.sightings):
        if not s.date or not s.location:
            return _fail(label, f"Sighting {i} missing date or location")
    return _pass(label, f"{result.match_count} match(es), {len(result.sightings)} sighting(s)")


async def check_stopice_detail(plate: str) -> bool:
    """Check 2: stopice.net detail page."""
    label = "stopice.net detail page"
    result: LookupResult = await fetch_descriptions(plate)
    if result.error:
        return _fail(label, f"Error: {result.error}")
    if not result.sightings:
        return _fail(label, "No sightings parsed from detail page")
    for i, s in enumerate(result.sightings):
        if not s.date or not s.location:
            return _fail(label, f"Sighting {i} missing date or location")
    has_vehicle_or_desc = any(s.vehicle or s.description for s in result.sightings)
    if not has_vehicle_or_desc:
        return _fail(label, "No sighting has vehicle or description")
    return _pass(label, f"{len(result.sightings)} sighting(s) with vehicle/description data")


async def check_defrost_meta() -> bool:
    """Check 3: defrostmn.net meta fetch."""
    label = "defrostmn.net meta fetch"
    meta, error = await fetch_meta()
    if error:
        return _fail(label, f"Error: {error}")
    if not isinstance(meta, dict):
        return _fail(label, "Response is not a JSON object")
    rotation = meta.get("rotation")
    if not isinstance(rotation, int):
        return _fail(label, f"rotation={rotation!r}, expected int")
    num_pages = meta.get("numPages")
    if not isinstance(num_pages, int) or num_pages < 1:
        return _fail(label, f"numPages={num_pages!r}, expected int >= 1")
    updated = meta.get("updated")
    if not updated or not isinstance(updated, str):
        return _fail(label, f"updated={updated!r}, expected non-empty string")
    return _pass(label, f"rotation={rotation}, numPages={num_pages}, updated={updated}")


async def check_defrost_pages() -> bool | None:
    """Check 4: defrostmn.net page decryption."""
    label = "defrostmn.net page decryption"
    if not get_decrypt_key():
        _skip(label, "DEFROST_DECRYPT_KEY not set")
        return None

    meta, error = await fetch_meta()
    if error:
        return _fail(label, f"Meta fetch failed: {error}")

    rotation = meta.get("rotation", 1)
    num_pages = meta.get("numPages", 1)
    records, errors = await fetch_all_pages(rotation, num_pages)

    if errors:
        return _fail(label, f"{len(errors)} page error(s): {'; '.join(errors[:3])}")
    if not records:
        return _fail(label, "No records after decrypting all pages")
    # Validate record structure
    for i, rec in enumerate(records[:5]):
        fields = rec.get("fields")
        if not fields or "Plate" not in fields:
            return _fail(label, f"Record {i} missing fields.Plate")
    return _pass(label, f"{len(records)} record(s) from {num_pages} page(s), 0 errors")


async def check_defrost_stopice_json() -> bool | None:
    """Check 5: defrostmn.net stopice JSON snapshot."""
    label = "defrostmn.net stopice JSON"
    url = get_defrost_url()
    if not url:
        _skip(label, "DEFROST_JSON_URL not set")
        return None

    body, error = await fetch_with_retry("GET", url)
    if error:
        return _fail(label, f"Error: {error}")
    try:
        data = json.loads(body)
    except (ValueError, TypeError) as e:
        return _fail(label, f"Invalid JSON: {e}")
    plates = data.get("plates")
    if not isinstance(plates, list):
        return _fail(label, "Missing or invalid 'plates' list")
    if not plates:
        return _fail(label, "plates list is empty")
    # Validate structure of first few entries
    for i, entry in enumerate(plates[:5]):
        if "license_plate" not in entry:
            return _fail(label, f"Entry {i} missing 'license_plate'")
        if "records" not in entry:
            return _fail(label, f"Entry {i} missing 'records'")
    return _pass(label, f"{len(plates)} plate(s) in snapshot")


async def check_defrost_full_lookup(plate: str) -> bool | None:
    """Check 6: defrostmn.net full plate lookup (merge logic)."""
    label = "defrostmn.net full plate lookup"
    if not get_decrypt_key() and not get_defrost_url():
        _skip(label, "Neither DEFROST_DECRYPT_KEY nor DEFROST_JSON_URL set")
        return None

    result: LookupResult = await check_plate_defrost(plate)
    if result.error:
        return _fail(label, f"Error: {result.error}")
    if result.found:
        return _pass(
            label, f"Found: {result.match_count} match(es), {len(result.sightings)} sighting(s)"
        )
    return _pass(label, f"Plate {plate!r} not in defrost (not an error)")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Health-check for live data sources")
    parser.add_argument(
        "plate",
        nargs="?",
        default=None,
        help="Known plate to check (overrides CHECK_PLATE env var)",
    )
    args = parser.parse_args()

    plate = args.plate or os.environ.get("CHECK_PLATE", "")
    if not plate:
        print(f"{RED}Error: No plate provided.{RESET}")
        print("Usage: python check_sources.py PLATE  or  CHECK_PLATE=PLATE python check_sources.py")
        return 2

    plate = plate.upper()

    # Clear caches to force live requests
    clear_caches()

    # Show WARNING-level logs so retry messages are visible
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    print(f"\n{BOLD}Health check for plate: {plate}{RESET}\n")

    passed = 0
    failed = 0
    skipped = 0

    # --- stopice.net ---
    print(f"{BOLD}stopice.net{RESET}")
    checks_stopice = [
        await check_stopice_search(plate),
        await check_stopice_detail(plate),
    ]
    for result in checks_stopice:
        if result:
            passed += 1
        else:
            failed += 1

    # --- defrostmn.net ---
    print(f"\n{BOLD}defrostmn.net{RESET}")
    checks_defrost = [
        await check_defrost_meta(),
        await check_defrost_pages(),
        await check_defrost_stopice_json(),
        await check_defrost_full_lookup(plate),
    ]
    for result in checks_defrost:
        if result is None:
            skipped += 1
        elif result:
            passed += 1
        else:
            failed += 1

    # --- Summary ---
    total = passed + failed
    print(f"\n{BOLD}Summary:{RESET} {passed}/{total} passed", end="")
    if skipped:
        print(f", {skipped} skipped", end="")
    if failed:
        print(f"  {RED}FAILED{RESET}")
    else:
        print(f"  {GREEN}OK{RESET}")
    print()

    await close_session()

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

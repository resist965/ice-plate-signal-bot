import asyncio
import json
import logging
import re
import time

from signalbot import Command, Context, regex_triggered

logger = logging.getLogger(__name__)

from lookup import check_plate, fetch_descriptions, LookupResult, Sighting
from lookup_defrost import check_plate_defrost
from ocr import extract_plate_from_image, OCRError

_PENDING_TTL = 3600  # 1 hour


class PlateCommand(Command):
    def setup(self) -> None:
        # Maps reply timestamp -> (plate, created_time, sources_with_matches)
        self._pending: dict[int, tuple[str, float, set[str]]] = {}

    def get_pending_plate(self, ts: int) -> str | None:
        """Return the plate string for a pending timestamp, or None."""
        entry = self._pending.get(ts)
        return entry[0] if entry else None

    def get_pending_sources(self, ts: int) -> set[str]:
        """Return the set of matched sources for a pending timestamp."""
        entry = self._pending.get(ts)
        return entry[2] if entry else set()

    def resolve_pending(self, ts: int) -> str | None:
        """Remove and return the plate string for a pending timestamp, or None."""
        entry = self._pending.pop(ts, None)
        return entry[0] if entry else None

    def _cleanup_pending(self) -> None:
        """Purge pending entries older than _PENDING_TTL."""
        cutoff = time.time() - _PENDING_TTL
        expired = [ts for ts, (_, created, _sources) in self._pending.items() if created < cutoff]
        for ts in expired:
            del self._pending[ts]

    @regex_triggered(r"^/plate\b")
    async def handle(self, c: Context) -> None:
        self._cleanup_pending()
        await c.react("\U0001f440")  # 👀

        parts = c.message.text.split(maxsplit=1)
        has_text = len(parts) > 1 and parts[1].strip()
        has_image = bool(c.message.base64_attachments)

        if has_text:
            raw_plate = parts[1].strip().upper()
        elif has_image:
            try:
                raw_plate = await extract_plate_from_image(
                    c.message.base64_attachments[0]
                )
            except OCRError as e:
                await c.send(f"Could not read plate from image: {e}")
                return
            except Exception:
                logger.exception("Unexpected error during OCR processing")
                await c.send(
                    "Could not read plate from image: an unexpected error occurred."
                )
                return
            await c.send(f"Detected plate: {raw_plate} — searching now...")
        else:
            await c.send(
                "Usage: /plate ABC123 or send /plate with an image of a license plate."
            )
            return

        if not raw_plate or not re.match(r"^[A-Z0-9 \-]+$", raw_plate):
            await c.send("Invalid plate format. Use letters, numbers, spaces, or hyphens.")
            return

        stopice_result, defrost_result = await asyncio.gather(
            check_plate(raw_plate),
            check_plate_defrost(raw_plate),
        )

        lines = []
        sources_with_matches: set[str] = set()

        # Format stopice.net result
        lines.append(_format_source_result("stopice.net", stopice_result))
        if stopice_result.found:
            sources_with_matches.add("stopice")

        # Format defrostmn.net result
        lines.append(_format_source_result("defrostmn.net", defrost_result))
        if defrost_result.found:
            sources_with_matches.add("defrost")

        if sources_with_matches:
            lines.append("\nReact \U0001f440 to this message for full descriptions.")
            ts = await c.reply("\n".join(lines))
            self._pending[ts] = (raw_plate, time.time(), sources_with_matches)
        else:
            await c.reply("\n".join(lines))


class PlateDetailCommand(Command):
    """Watches for 👀 reactions on plate results and sends full descriptions."""

    def setup(self) -> None:
        self._plate_cmd = None

    def set_plate_command(self, plate_cmd: PlateCommand) -> None:
        self._plate_cmd = plate_cmd

    async def handle(self, c: Context) -> None:
        if not c.message.reaction or c.message.reaction != "\U0001f440":
            return
        if not self._plate_cmd:
            return

        target_ts = _extract_reaction_target_ts(c.message.raw_message)
        if target_ts is None:
            return

        plate = self._plate_cmd.get_pending_plate(target_ts)
        if not plate:
            return

        sources = self._plate_cmd.get_pending_sources(target_ts)

        # Build tasks for matched sources
        tasks = {}
        if "stopice" in sources:
            tasks["stopice"] = fetch_descriptions(plate)
        if "defrost" in sources:
            tasks["defrost"] = check_plate_defrost(plate)

        results = {}
        if tasks:
            keys = list(tasks.keys())
            gathered = await asyncio.gather(*tasks.values())
            results = dict(zip(keys, gathered))

        lines = [f"Details for {plate}:"]
        any_sightings = False

        if "stopice" in results:
            result = results["stopice"]
            if result.error:
                lines.append(f"\n--- stopice.net ---")
                lines.append(f"Error: {result.error}")
                lines.append(f"https://www.stopice.net/platetracker/index.cgi?plate={plate}")
            elif result.sightings:
                any_sightings = True
                lines.append(f"\n--- stopice.net ---")
                lines.extend(_format_sighting_details(result.sightings))
                lines.append(f"\nhttps://www.stopice.net/platetracker/index.cgi?plate={plate}")
            else:
                lines.append(f"\n--- stopice.net ---")
                lines.append(f"No sightings found on the detail page.")
                lines.append(f"https://www.stopice.net/platetracker/index.cgi?plate={plate}")

        if "defrost" in results:
            result = results["defrost"]
            if result.error:
                lines.append(f"\n--- defrostmn.net ---")
                lines.append(f"Error: {result.error}")
            elif result.sightings:
                any_sightings = True
                lines.append(f"\n--- defrostmn.net ---")
                lines.extend(_format_sighting_details(result.sightings))

        if any_sightings:
            self._plate_cmd.resolve_pending(target_ts)

        await c.send("\n".join(lines))


def _format_source_result(source_name: str, result: LookupResult) -> str:
    """Format a single source's result for the initial reply."""
    if result.error:
        return f"\u274c {source_name} — Error: {result.error}"
    if result.found:
        line = f"\u26a0\ufe0f {source_name} — MATCH FOUND"
        if result.status:
            line += f" — {result.status}"
        return line
    return f"\u2714\ufe0f {source_name} — No match found."


def _format_sighting_details(sightings: list[Sighting]) -> list[str]:
    """Format sightings with full details for the detail reply."""
    lines = []
    for i, s in enumerate(sightings, 1):
        lines.append(f"\nSighting {i}:")
        if s.time:
            lines.append(f"Date: {s.time}")
        else:
            lines.append(f"Date: {s.date}")
        if s.location:
            lines.append(f"Location: {s.location}")
        if s.vehicle:
            lines.append(f"Vehicle: {s.vehicle}")
        if s.description:
            lines.append(f"Description: {s.description}")
    return lines


def _extract_reaction_target_ts(raw_message: str | None) -> int | None:
    """Extract targetSentTimestamp from a reaction's raw JSON.

    signal-cli-rest-api sends reaction events with the target info nested
    inside envelope.dataMessage.reaction or envelope.syncMessage.sentMessage.reaction.
    signalbot discards everything except the emoji, so we parse raw_message.
    """
    if not raw_message:
        return None
    try:
        data = json.loads(raw_message)
        envelope = data.get("envelope", data)
        for path in (
            ("dataMessage", "reaction"),
            ("syncMessage", "sentMessage", "reaction"),
        ):
            obj = envelope
            for key in path:
                obj = obj.get(key, {})
            ts = obj.get("targetSentTimestamp")
            if ts is not None:
                return int(ts)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return None

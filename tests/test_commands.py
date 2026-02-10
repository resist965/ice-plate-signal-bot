"""Tests for command handlers in commands/plate.py and commands/help.py."""

import json
import re
from unittest.mock import patch

from commands.help import HELP_TEXT, HelpCommand
from commands.plate import (
    PlateCommand,
    PlateDetailCommand,
    VoicePlateCommand,
    _extract_reaction_target_ts,
    _format_source_result,
    _is_voice_message,
)
from lookup import LookupResult, Sighting
from ocr import OCRError
from stt import STTError

# ---------------------------------------------------------------------------
# PlateCommand — pending state
# ---------------------------------------------------------------------------


class TestPlateCommandPending:
    def _make_cmd(self):
        cmd = PlateCommand.__new__(PlateCommand)
        cmd.setup()
        return cmd

    def test_setup_initializes_empty_pending(self):
        cmd = self._make_cmd()
        assert cmd._pending == {}

    def test_get_pending_plate_present(self):
        cmd = self._make_cmd()
        cmd._pending[100] = ("ABC123", 1000.0, {"stopice"})
        assert cmd.get_pending_plate(100) == "ABC123"

    def test_get_pending_plate_missing(self):
        cmd = self._make_cmd()
        assert cmd.get_pending_plate(999) is None

    def test_get_pending_sources_present(self):
        cmd = self._make_cmd()
        cmd._pending[100] = ("ABC123", 1000.0, {"stopice", "defrost"})
        assert cmd.get_pending_sources(100) == {"stopice", "defrost"}

    def test_get_pending_sources_missing(self):
        cmd = self._make_cmd()
        assert cmd.get_pending_sources(999) == set()

    def test_resolve_pending_returns_and_removes(self):
        cmd = self._make_cmd()
        cmd._pending[100] = ("ABC123", 1000.0, {"stopice"})
        assert cmd.resolve_pending(100) == "ABC123"
        assert 100 not in cmd._pending

    def test_resolve_pending_missing(self):
        cmd = self._make_cmd()
        assert cmd.resolve_pending(999) is None

    @patch("commands.plate.time.time", return_value=10000.0)
    def test_cleanup_pending_removes_old(self, _mock_time):
        cmd = self._make_cmd()
        cmd._pending[1] = ("OLD", 1.0, {"stopice"})  # expired (10000 - 3600 = 6400 > 1.0)
        cmd._pending[2] = ("RECENT", 9999.0, {"defrost"})  # still valid
        cmd._cleanup_pending()
        assert 1 not in cmd._pending
        assert 2 in cmd._pending

    @patch("commands.plate.time.time", return_value=10000.0)
    def test_cleanup_pending_keeps_recent(self, _mock_time):
        cmd = self._make_cmd()
        cmd._pending[1] = ("RECENT", 9500.0, {"stopice"})
        cmd._cleanup_pending()
        assert 1 in cmd._pending


# ---------------------------------------------------------------------------
# PlateCommand.handle()
# ---------------------------------------------------------------------------


class TestPlateCommandHandle:
    def _make_cmd(self):
        cmd = PlateCommand.__new__(PlateCommand)
        cmd.setup()
        return cmd

    @patch("commands.plate.check_plate_defrost")
    @patch("commands.plate.check_plate")
    async def test_valid_plate_match(self, mock_check, mock_defrost, mock_context):
        mock_check.return_value = LookupResult(
            found=True,
            match_count=1,
            record_count=3,
            sightings=[Sighting(date="JAN 1 2026", location="CITY A")],
        )
        mock_defrost.return_value = LookupResult(found=False)
        ctx = mock_context(text="/plate SXF180")
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        ctx.react.assert_called_once_with("\U0001f440")
        reply_text = ctx.reply.call_args[0][0]
        assert "MATCH FOUND" in reply_text
        assert 1234567890 in cmd._pending
        assert cmd.get_pending_sources(1234567890) == {"stopice"}

    @patch("commands.plate.check_plate_defrost")
    @patch("commands.plate.check_plate")
    async def test_valid_plate_no_match(self, mock_check, mock_defrost, mock_context):
        mock_check.return_value = LookupResult(found=False)
        mock_defrost.return_value = LookupResult(found=False)
        ctx = mock_context(text="/plate ZZZZ000")
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        reply_text = ctx.reply.call_args[0][0]
        assert "No match found" in reply_text

    @patch("commands.plate.check_plate_defrost")
    @patch("commands.plate.check_plate")
    async def test_valid_plate_error(self, mock_check, mock_defrost, mock_context):
        mock_check.return_value = LookupResult(found=False, error="Lookup service unavailable")
        mock_defrost.return_value = LookupResult(found=False)
        ctx = mock_context(text="/plate ABC123")
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        reply_text = ctx.reply.call_args[0][0]
        assert "Lookup service unavailable" in reply_text

    async def test_invalid_plate_format(self, mock_context):
        ctx = mock_context(text="/plate ABC@123")
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        ctx.send.assert_called_once()
        assert "Invalid plate format" in ctx.send.call_args[0][0]

    async def test_invalid_plate_still_reacts(self, mock_context):
        """react("👀") fires before validation, so invalid plates still get it."""
        ctx = mock_context(text="/plate ABC@123")
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        ctx.react.assert_called_once_with("\U0001f440")

    @patch("commands.plate.check_plate_defrost")
    @patch("commands.plate.check_plate")
    async def test_both_sources_match(self, mock_check, mock_defrost, mock_context):
        mock_check.return_value = LookupResult(
            found=True,
            match_count=1,
            record_count=2,
            sightings=[Sighting(date="JAN 1 2026", location="CITY A")],
        )
        mock_defrost.return_value = LookupResult(
            found=True,
            match_count=1,
            record_count=1,
            sightings=[Sighting(date="FEB 1 2026", location="CITY B")],
        )
        ctx = mock_context(text="/plate SXF180")
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        reply_text = ctx.reply.call_args[0][0]
        assert "stopice.net" in reply_text
        assert "defrostmn.net" in reply_text
        assert reply_text.count("MATCH FOUND") == 2
        assert cmd.get_pending_sources(1234567890) == {"stopice", "defrost"}

    @patch("commands.plate.check_plate_defrost")
    @patch("commands.plate.check_plate")
    async def test_only_defrost_matches(self, mock_check, mock_defrost, mock_context):
        mock_check.return_value = LookupResult(found=False)
        mock_defrost.return_value = LookupResult(
            found=True,
            match_count=1,
            record_count=1,
            sightings=[Sighting(date="FEB 1 2026", location="CITY B")],
        )
        ctx = mock_context(text="/plate SXF180")
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        assert 1234567890 in cmd._pending
        assert cmd.get_pending_sources(1234567890) == {"defrost"}

    @patch("commands.plate.check_plate_defrost")
    @patch("commands.plate.check_plate")
    async def test_neither_matches_no_pending(self, mock_check, mock_defrost, mock_context):
        mock_check.return_value = LookupResult(found=False)
        mock_defrost.return_value = LookupResult(found=False)
        ctx = mock_context(text="/plate ZZZZ000")
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        assert 1234567890 not in cmd._pending

    @patch("commands.plate.check_plate_defrost")
    @patch("commands.plate.check_plate")
    async def test_one_errors_one_matches(self, mock_check, mock_defrost, mock_context):
        mock_check.return_value = LookupResult(found=False, error="Service down")
        mock_defrost.return_value = LookupResult(
            found=True,
            match_count=1,
            record_count=1,
            sightings=[Sighting(date="FEB 1 2026", location="CITY B")],
        )
        ctx = mock_context(text="/plate SXF180")
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        reply_text = ctx.reply.call_args[0][0]
        assert "Error: Service down" in reply_text
        assert "MATCH FOUND" in reply_text
        assert cmd.get_pending_sources(1234567890) == {"defrost"}


# ---------------------------------------------------------------------------
# PlateCommand — image OCR
# ---------------------------------------------------------------------------


class TestPlateCommandOCR:
    def _make_cmd(self):
        cmd = PlateCommand.__new__(PlateCommand)
        cmd.setup()
        return cmd

    @patch("commands.plate.check_plate_defrost")
    @patch("commands.plate.check_plate")
    @patch("commands.plate.extract_plate_from_image")
    async def test_image_triggers_ocr(self, mock_ocr, mock_check, mock_defrost, mock_context):
        mock_ocr.return_value = "ABC123"
        mock_check.return_value = LookupResult(found=False)
        mock_defrost.return_value = LookupResult(found=False)
        ctx = mock_context(text="/plate", base64_attachments=["aW1hZ2VkYXRh"])
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        mock_ocr.assert_called_once_with("aW1hZ2VkYXRh")
        mock_check.assert_called_once_with("ABC123")
        mock_defrost.assert_called_once_with("ABC123")
        # Confirm detected plate message was sent
        send_calls = [call[0][0] for call in ctx.send.call_args_list]
        assert any("Detected plate: ABC123" in msg and "searching" in msg for msg in send_calls)

    @patch("commands.plate.extract_plate_from_image")
    async def test_ocr_error_sends_message(self, mock_ocr, mock_context):
        mock_ocr.side_effect = OCRError("Could not read any text from the image.")
        ctx = mock_context(text="/plate", base64_attachments=["aW1hZ2VkYXRh"])
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        send_text = ctx.send.call_args[0][0]
        assert "Could not read plate from image" in send_text

    async def test_no_text_no_image_sends_usage(self, mock_context):
        ctx = mock_context(text="/plate")
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        send_text = ctx.send.call_args[0][0]
        assert "Usage:" in send_text

    @patch("commands.plate.check_plate_defrost")
    @patch("commands.plate.check_plate")
    @patch("commands.plate.extract_plate_from_image")
    async def test_text_takes_priority_over_image(
        self, mock_ocr, mock_check, mock_defrost, mock_context
    ):
        mock_check.return_value = LookupResult(found=False)
        mock_defrost.return_value = LookupResult(found=False)
        ctx = mock_context(text="/plate XYZ789", base64_attachments=["aW1hZ2VkYXRh"])
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        mock_ocr.assert_not_called()
        mock_check.assert_called_once_with("XYZ789")

    def test_regex_matches_bare_plate(self):
        assert re.search(r"^/plate\b", "/plate")

    def test_regex_matches_plate_with_text(self):
        assert re.search(r"^/plate\b", "/plate ABC123")

    def test_regex_no_match_plateinfo(self):
        assert re.search(r"^/plate\b", "/plateinfo") is None

    @patch("commands.plate.extract_plate_from_image")
    async def test_unexpected_error_sends_message(self, mock_ocr, mock_context):
        mock_ocr.side_effect = RuntimeError("Model inference failed")
        ctx = mock_context(text="/plate", base64_attachments=["aW1hZ2VkYXRh"])
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        send_text = ctx.send.call_args[0][0]
        assert "Could not read plate from image" in send_text

    @patch("commands.plate.check_plate_defrost")
    @patch("commands.plate.check_plate")
    @patch("commands.plate.extract_plate_from_image")
    async def test_trailing_space_with_image_triggers_ocr(
        self, mock_ocr, mock_check, mock_defrost, mock_context
    ):
        mock_ocr.return_value = "ABC123"
        mock_check.return_value = LookupResult(found=False)
        mock_defrost.return_value = LookupResult(found=False)
        ctx = mock_context(text="/plate ", base64_attachments=["aW1hZ2VkYXRh"])
        cmd = self._make_cmd()
        await cmd.handle(ctx)

        mock_ocr.assert_called_once_with("aW1hZ2VkYXRh")
        mock_check.assert_called_once_with("ABC123")


# ---------------------------------------------------------------------------
# PlateDetailCommand.handle()
# ---------------------------------------------------------------------------


class TestPlateDetailCommandHandle:
    def _make_detail_cmd(self, plate_cmd=None):
        cmd = PlateDetailCommand.__new__(PlateDetailCommand)
        cmd.setup()
        if plate_cmd:
            cmd.set_plate_command(plate_cmd)
        return cmd

    async def test_no_reaction_returns_early(self, mock_context):
        ctx = mock_context(reaction=None)
        cmd = self._make_detail_cmd()
        await cmd.handle(ctx)
        ctx.send.assert_not_called()

    async def test_wrong_emoji_returns_early(self, mock_context):
        ctx = mock_context(reaction="\u2764\ufe0f")
        cmd = self._make_detail_cmd()
        await cmd.handle(ctx)
        ctx.send.assert_not_called()

    async def test_no_plate_cmd_returns_early(self, mock_context):
        ctx = mock_context(reaction="\U0001f440")
        cmd = self._make_detail_cmd(plate_cmd=None)
        await cmd.handle(ctx)
        ctx.send.assert_not_called()

    @patch("commands.plate.fetch_descriptions")
    async def test_valid_reaction_stopice_success(self, mock_fetch, mock_context):
        plate_cmd = PlateCommand.__new__(PlateCommand)
        plate_cmd.setup()
        plate_cmd._pending[555] = ("SXF180", 1000.0, {"stopice"})

        mock_fetch.return_value = LookupResult(
            found=True,
            sightings=[
                Sighting(
                    date="JAN 1", location="CITY", vehicle="MAZDA", description="desc", time="10:00"
                )
            ],
        )

        raw = json.dumps({"envelope": {"dataMessage": {"reaction": {"targetSentTimestamp": 555}}}})
        ctx = mock_context(reaction="\U0001f440", raw_message=raw)

        cmd = self._make_detail_cmd(plate_cmd)
        await cmd.handle(ctx)

        ctx.send.assert_called_once()
        text = ctx.send.call_args[0][0]
        assert "Details for SXF180" in text
        assert "--- stopice.net ---" in text
        assert "MAZDA" in text
        assert 555 not in plate_cmd._pending

    async def test_no_pending_plate(self, mock_context):
        plate_cmd = PlateCommand.__new__(PlateCommand)
        plate_cmd.setup()

        raw = json.dumps({"envelope": {"dataMessage": {"reaction": {"targetSentTimestamp": 555}}}})
        ctx = mock_context(reaction="\U0001f440", raw_message=raw)

        cmd = self._make_detail_cmd(plate_cmd)
        await cmd.handle(ctx)
        ctx.send.assert_not_called()

    @patch("commands.plate.fetch_descriptions")
    async def test_no_sightings_sends_url(self, mock_fetch, mock_context):
        """Detail page loaded OK but contained no parseable sightings."""
        plate_cmd = PlateCommand.__new__(PlateCommand)
        plate_cmd.setup()
        plate_cmd._pending[555] = ("SXF180", 1000.0, {"stopice"})

        mock_fetch.return_value = LookupResult(found=False, sightings=[])

        raw = json.dumps({"envelope": {"dataMessage": {"reaction": {"targetSentTimestamp": 555}}}})
        ctx = mock_context(reaction="\U0001f440", raw_message=raw)

        cmd = self._make_detail_cmd(plate_cmd)
        await cmd.handle(ctx)

        ctx.send.assert_called_once()
        text = ctx.send.call_args[0][0]
        assert "No sightings found" in text
        assert "plate=SXF180" in text

    @patch("commands.plate.fetch_descriptions")
    async def test_fetch_error_sends_url(self, mock_fetch, mock_context):
        plate_cmd = PlateCommand.__new__(PlateCommand)
        plate_cmd.setup()
        plate_cmd._pending[555] = ("SXF180", 1000.0, {"stopice"})

        mock_fetch.return_value = LookupResult(found=False, error="Could not reach lookup service")

        raw = json.dumps({"envelope": {"dataMessage": {"reaction": {"targetSentTimestamp": 555}}}})
        ctx = mock_context(reaction="\U0001f440", raw_message=raw)

        cmd = self._make_detail_cmd(plate_cmd)
        await cmd.handle(ctx)

        ctx.send.assert_called_once()
        text = ctx.send.call_args[0][0]
        assert "Error" in text
        assert "plate=SXF180" in text

    @patch("commands.plate.check_plate_defrost")
    @patch("commands.plate.fetch_descriptions")
    async def test_detail_both_sources(self, mock_fetch, mock_defrost, mock_context):
        """Detail fetch from both sources shows both source headers."""
        plate_cmd = PlateCommand.__new__(PlateCommand)
        plate_cmd.setup()
        plate_cmd._pending[555] = ("SXF180", 1000.0, {"stopice", "defrost"})

        mock_fetch.return_value = LookupResult(
            found=True,
            sightings=[
                Sighting(
                    date="JAN 1",
                    location="CITY A",
                    vehicle="MAZDA",
                    description="desc1",
                    time="10:00",
                )
            ],
        )
        mock_defrost.return_value = LookupResult(
            found=True,
            sightings=[
                Sighting(
                    date="FEB 1",
                    location="CITY B",
                    vehicle="Honda",
                    description="desc2",
                    time="14:00",
                )
            ],
        )

        raw = json.dumps({"envelope": {"dataMessage": {"reaction": {"targetSentTimestamp": 555}}}})
        ctx = mock_context(reaction="\U0001f440", raw_message=raw)

        cmd = self._make_detail_cmd(plate_cmd)
        await cmd.handle(ctx)

        ctx.send.assert_called_once()
        text = ctx.send.call_args[0][0]
        assert "--- stopice.net ---" in text
        assert "--- defrostmn.net ---" in text
        assert "MAZDA" in text
        assert "Honda" in text
        assert 555 not in plate_cmd._pending

    @patch("commands.plate.check_plate_defrost")
    async def test_detail_defrost_only(self, mock_defrost, mock_context):
        """Detail fetch with only defrost source."""
        plate_cmd = PlateCommand.__new__(PlateCommand)
        plate_cmd.setup()
        plate_cmd._pending[555] = ("TEST123", 1000.0, {"defrost"})

        mock_defrost.return_value = LookupResult(
            found=True,
            sightings=[
                Sighting(
                    date="FEB 1",
                    location="CITY B",
                    vehicle="Honda",
                    description="desc",
                    time="14:00",
                )
            ],
        )

        raw = json.dumps({"envelope": {"dataMessage": {"reaction": {"targetSentTimestamp": 555}}}})
        ctx = mock_context(reaction="\U0001f440", raw_message=raw)

        cmd = self._make_detail_cmd(plate_cmd)
        await cmd.handle(ctx)

        ctx.send.assert_called_once()
        text = ctx.send.call_args[0][0]
        assert "--- defrostmn.net ---" in text
        assert "stopice.net" not in text
        assert 555 not in plate_cmd._pending


# ---------------------------------------------------------------------------
# _format_source_result
# ---------------------------------------------------------------------------


class TestFormatSourceResult:
    def test_match_found(self):
        result = LookupResult(found=True, match_count=1, record_count=3)
        text = _format_source_result("stopice.net", result)
        assert "MATCH FOUND" in text
        assert "stopice.net" in text

    def test_match_found_with_status(self):
        result = LookupResult(found=True, match_count=1, record_count=1, status="Confirmed ICE")
        text = _format_source_result("defrostmn.net", result)
        assert "MATCH FOUND" in text
        assert "Confirmed ICE" in text

    def test_match_found_without_status(self):
        result = LookupResult(found=True, match_count=1, record_count=1)
        text = _format_source_result("stopice.net", result)
        assert "MATCH FOUND" in text
        assert text.count("—") == 1  # Only one dash separator, no status

    def test_no_match(self):
        result = LookupResult(found=False)
        text = _format_source_result("defrostmn.net", result)
        assert "No match found" in text
        assert "defrostmn.net" in text

    def test_error(self):
        result = LookupResult(found=False, error="Service down")
        text = _format_source_result("stopice.net", result)
        assert "Error: Service down" in text
        assert "stopice.net" in text


# ---------------------------------------------------------------------------
# _extract_reaction_target_ts
# ---------------------------------------------------------------------------


class TestExtractReactionTargetTs:
    def test_data_message_path(self):
        raw = json.dumps(
            {"envelope": {"dataMessage": {"reaction": {"targetSentTimestamp": 12345}}}}
        )
        assert _extract_reaction_target_ts(raw) == 12345

    def test_sync_message_path(self):
        raw = json.dumps(
            {
                "envelope": {
                    "syncMessage": {"sentMessage": {"reaction": {"targetSentTimestamp": 67890}}}
                }
            }
        )
        assert _extract_reaction_target_ts(raw) == 67890

    def test_none_input(self):
        assert _extract_reaction_target_ts(None) is None

    def test_invalid_json(self):
        assert _extract_reaction_target_ts("not json") is None

    def test_json_without_reaction_keys(self):
        raw = json.dumps({"envelope": {"dataMessage": {"body": "hello"}}})
        assert _extract_reaction_target_ts(raw) is None


# ---------------------------------------------------------------------------
# HelpCommand
# ---------------------------------------------------------------------------


class TestHelpCommand:
    async def test_sends_help_text(self, mock_context):
        ctx = mock_context(text="/help")
        cmd = HelpCommand.__new__(HelpCommand)
        await cmd.handle(ctx)
        ctx.send.assert_called_once_with(HELP_TEXT)

    async def test_help_mentions_voice(self, mock_context):
        assert "voice" in HELP_TEXT.lower() or "Voice" in HELP_TEXT


# ---------------------------------------------------------------------------
# _is_voice_message
# ---------------------------------------------------------------------------


class TestIsVoiceMessage:
    def test_voice_note_flag(self):
        raw = json.dumps(
            {
                "envelope": {
                    "dataMessage": {
                        "attachments": [{"contentType": "audio/aac", "voiceNote": True}]
                    }
                }
            }
        )
        assert _is_voice_message(raw) is True

    def test_audio_content_type_without_voice_note(self):
        raw = json.dumps(
            {"envelope": {"dataMessage": {"attachments": [{"contentType": "audio/ogg"}]}}}
        )
        assert _is_voice_message(raw) is True

    def test_image_attachment_not_voice(self):
        raw = json.dumps(
            {"envelope": {"dataMessage": {"attachments": [{"contentType": "image/jpeg"}]}}}
        )
        assert _is_voice_message(raw) is False

    def test_no_attachments(self):
        raw = json.dumps({"envelope": {"dataMessage": {"body": "hello"}}})
        assert _is_voice_message(raw) is False

    def test_none_raw_message(self):
        assert _is_voice_message(None) is False

    def test_invalid_json(self):
        assert _is_voice_message("not json") is False

    def test_sync_message_path(self):
        raw = json.dumps(
            {
                "envelope": {
                    "syncMessage": {
                        "sentMessage": {
                            "attachments": [{"contentType": "audio/aac", "voiceNote": True}]
                        }
                    }
                }
            }
        )
        assert _is_voice_message(raw) is True

    def test_empty_attachments(self):
        raw = json.dumps({"envelope": {"dataMessage": {"attachments": []}}})
        assert _is_voice_message(raw) is False

    def test_voice_note_false_with_audio_type_not_voice(self):
        """Audio attachment with voiceNote explicitly False is not a voice message."""
        raw = json.dumps(
            {
                "envelope": {
                    "dataMessage": {
                        "attachments": [{"contentType": "audio/mpeg", "voiceNote": False}]
                    }
                }
            }
        )
        assert _is_voice_message(raw) is False


# ---------------------------------------------------------------------------
# VoicePlateCommand.handle()
# ---------------------------------------------------------------------------


class TestVoicePlateCommandHandle:
    def _make_voice_cmd(self, plate_cmd=None):
        cmd = VoicePlateCommand.__new__(VoicePlateCommand)
        cmd.setup()
        if plate_cmd:
            cmd.set_plate_command(plate_cmd)
        return cmd

    def _make_plate_cmd(self):
        cmd = PlateCommand.__new__(PlateCommand)
        cmd.setup()
        return cmd

    def _voice_raw(self):
        return json.dumps(
            {
                "envelope": {
                    "dataMessage": {
                        "attachments": [{"contentType": "audio/aac", "voiceNote": True}]
                    }
                }
            }
        )

    async def test_non_voice_message_returns_early(self, mock_context):
        plate_cmd = self._make_plate_cmd()
        voice_cmd = self._make_voice_cmd(plate_cmd)
        raw = json.dumps(
            {"envelope": {"dataMessage": {"attachments": [{"contentType": "image/jpeg"}]}}}
        )
        ctx = mock_context(raw_message=raw, base64_attachments=["aW1hZ2VkYXRh"])
        await voice_cmd.handle(ctx)
        ctx.react.assert_not_called()

    async def test_no_plate_cmd_returns_early(self, mock_context):
        voice_cmd = self._make_voice_cmd()
        ctx = mock_context(raw_message=self._voice_raw(), base64_attachments=["YXVkaW8="])
        await voice_cmd.handle(ctx)
        ctx.react.assert_not_called()

    async def test_no_attachment_data_returns_early(self, mock_context):
        plate_cmd = self._make_plate_cmd()
        voice_cmd = self._make_voice_cmd(plate_cmd)
        ctx = mock_context(raw_message=self._voice_raw(), base64_attachments=[])
        await voice_cmd.handle(ctx)
        ctx.react.assert_not_called()

    @patch("commands.plate.check_plate_defrost")
    @patch("commands.plate.check_plate")
    @patch("commands.plate.extract_plate_from_voice")
    async def test_voice_triggers_lookup(self, mock_stt, mock_check, mock_defrost, mock_context):
        mock_stt.return_value = "ABC123"
        mock_check.return_value = LookupResult(found=False)
        mock_defrost.return_value = LookupResult(found=False)

        plate_cmd = self._make_plate_cmd()
        voice_cmd = self._make_voice_cmd(plate_cmd)
        ctx = mock_context(raw_message=self._voice_raw(), base64_attachments=["YXVkaW8="])
        await voice_cmd.handle(ctx)

        ctx.react.assert_called_once_with("\U0001f3a4")
        mock_stt.assert_called_once_with("YXVkaW8=")
        mock_check.assert_called_once_with("ABC123")
        send_calls = [call[0][0] for call in ctx.send.call_args_list]
        assert any("Detected plate: ABC123" in msg for msg in send_calls)

    @patch("commands.plate.check_plate_defrost")
    @patch("commands.plate.check_plate")
    @patch("commands.plate.extract_plate_from_voice")
    async def test_voice_match_creates_pending(
        self, mock_stt, mock_check, mock_defrost, mock_context
    ):
        mock_stt.return_value = "SXF180"
        mock_check.return_value = LookupResult(
            found=True,
            match_count=1,
            record_count=3,
            sightings=[Sighting(date="JAN 1 2026", location="CITY A")],
        )
        mock_defrost.return_value = LookupResult(found=False)

        plate_cmd = self._make_plate_cmd()
        voice_cmd = self._make_voice_cmd(plate_cmd)
        ctx = mock_context(raw_message=self._voice_raw(), base64_attachments=["YXVkaW8="])
        await voice_cmd.handle(ctx)

        assert 1234567890 in plate_cmd._pending
        assert plate_cmd.get_pending_plate(1234567890) == "SXF180"

    @patch("commands.plate.extract_plate_from_voice")
    async def test_stt_error_sends_message(self, mock_stt, mock_context):
        mock_stt.side_effect = STTError("Could not transcribe any speech")

        plate_cmd = self._make_plate_cmd()
        voice_cmd = self._make_voice_cmd(plate_cmd)
        ctx = mock_context(raw_message=self._voice_raw(), base64_attachments=["YXVkaW8="])
        await voice_cmd.handle(ctx)

        send_text = ctx.send.call_args[0][0]
        assert "Could not read plate from voice message" in send_text

    @patch("commands.plate.extract_plate_from_voice")
    async def test_unexpected_error_sends_message(self, mock_stt, mock_context):
        mock_stt.side_effect = RuntimeError("Model crashed")

        plate_cmd = self._make_plate_cmd()
        voice_cmd = self._make_voice_cmd(plate_cmd)
        ctx = mock_context(raw_message=self._voice_raw(), base64_attachments=["YXVkaW8="])
        await voice_cmd.handle(ctx)

        send_text = ctx.send.call_args[0][0]
        assert "Could not read plate from voice message" in send_text

    @patch("commands.plate.time.time", return_value=10000.0)
    @patch("commands.plate.check_plate_defrost")
    @patch("commands.plate.check_plate")
    @patch("commands.plate.extract_plate_from_voice")
    async def test_voice_cleans_up_pending(
        self, mock_stt, mock_check, mock_defrost, _mock_time, mock_context
    ):
        """VoicePlateCommand triggers _cleanup_pending to avoid memory leaks."""
        mock_stt.return_value = "ABC123"
        mock_check.return_value = LookupResult(found=False)
        mock_defrost.return_value = LookupResult(found=False)

        plate_cmd = self._make_plate_cmd()
        plate_cmd._pending[1] = ("OLD", 1.0, {"stopice"})  # expired
        voice_cmd = self._make_voice_cmd(plate_cmd)
        ctx = mock_context(raw_message=self._voice_raw(), base64_attachments=["YXVkaW8="])
        await voice_cmd.handle(ctx)

        assert 1 not in plate_cmd._pending

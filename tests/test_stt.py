"""Tests for stt.py speech-to-text module."""

import base64
from unittest.mock import MagicMock, patch

import pytest

from stt import (
    STTError,
    _confusion_variants,
    _extract_plate_from_text,
    _load_noise_words,
    _matches_plate_format,
    _merge_single_chars,
    _normalize_words,
    _score_candidate,
    extract_plate_from_voice,
)

# ---------------------------------------------------------------------------
# _extract_plate_from_text
# ---------------------------------------------------------------------------


class TestExtractPlateFromText:
    def test_simple_plate(self):
        assert _extract_plate_from_text("ABC 1234") == "ABC1234"

    def test_plate_in_sentence(self):
        assert _extract_plate_from_text("the plate is ABC 1234") == "ABC1234"

    def test_spelled_out_letters(self):
        """Adjacent words combine: 'A B C 1234' -> 'ABC1234' via 3-word + 1-word groups."""
        result = _extract_plate_from_text("A B C 1234")
        assert result == "ABC1234"

    def test_single_word_plate(self):
        assert _extract_plate_from_text("ABC1234") == "ABC1234"

    def test_prefers_mixed_alphanumeric(self):
        """When both all-letters and mixed candidates exist, prefer mixed."""
        result = _extract_plate_from_text("HELLO ABC123")
        assert result == "ABC123"

    def test_fallback_to_longest_no_mixed(self):
        """When no mixed candidates, fall back to longest."""
        result = _extract_plate_from_text("AB CD")
        assert result in ("AB", "CD", "ABCD")
        # ABCD is the 2-word group, which is longest
        assert result == "ABCD"

    def test_digits_only_accepted(self):
        result = _extract_plate_from_text("12345")
        assert result == "12345"

    def test_empty_text_raises(self):
        with pytest.raises(STTError, match="empty"):
            _extract_plate_from_text("")

    def test_no_valid_candidate_raises(self):
        with pytest.raises(STTError, match="No plate number found"):
            _extract_plate_from_text("um uh the a")

    def test_single_char_rejected(self):
        """Single-char words don't form valid 2-8 char plates alone."""
        with pytest.raises(STTError, match="No plate number found"):
            _extract_plate_from_text("I")

    def test_two_word_grouping(self):
        result = _extract_plate_from_text("ABC 123")
        assert result == "ABC123"

    def test_punctuation_stripped(self):
        result = _extract_plate_from_text("A-B-C 1.2.3.4")
        assert result == "ABC1234"

    def test_case_insensitive(self):
        assert _extract_plate_from_text("abc 1234") == "ABC1234"

    def test_plate_at_end_of_sentence(self):
        result = _extract_plate_from_text("look up plate number SXF180")
        assert result == "SXF180"

    def test_three_word_grouping(self):
        result = _extract_plate_from_text("SX F 180")
        assert result == "SXF180"

    def test_plate_in_sentence_with_car(self):
        """The original bug: 'car' adjacent to plate caused CARSXF18 extraction."""
        result = _extract_plate_from_text("i see a car the plate number is SXF180")
        assert result == "SXF180"

    def test_california_format(self):
        result = _extract_plate_from_text("1ABC234")
        assert result == "1ABC234"

    def test_format_preferred_over_longer_nonformat(self):
        """A candidate matching a US plate format should beat a longer non-format one."""
        # "XY" + "ABC123" group = "XYABC123" (8 chars, no format match)
        # "ABC123" alone = format match (ABC123 pattern)
        result = _extract_plate_from_text("XY ABC123")
        assert result == "ABC123"

    def test_noise_words_vehicle_terms(self):
        result = _extract_plate_from_text("car truck SXF180")
        assert result == "SXF180"

    def test_plate_containing_noise_word(self):
        """Plates like OF1234 where 'OF' is a noise word should still be found."""
        result = _extract_plate_from_text("the plate OF 1234")
        assert result == "OF1234"

    # --- New integration tests for normalization / merge / confusion ---

    def test_nato_normalization_integration(self):
        result = _extract_plate_from_text("alpha bravo charlie 1234")
        assert result == "ABC1234"

    def test_number_word_normalization_integration(self):
        result = _extract_plate_from_text("one two three A B C")
        assert result == "123ABC"

    def test_nato_and_numbers_combined_integration(self):
        result = _extract_plate_from_text("sierra xray foxtrot one eight zero")
        assert result == "SXF180"

    def test_number_words_not_normalized_without_adjacency(self):
        """Number words far from single-char tokens stay as noise words."""
        result = _extract_plate_from_text("one car plate ABC123")
        assert result == "ABC123"

    def test_single_char_merge_integration(self):
        result = _extract_plate_from_text("S X F 1 8 0")
        assert result == "SXF180"

    def test_merge_preserves_multi_char(self):
        """Multi-char tokens break single-char merge runs."""
        result = _extract_plate_from_text("I SEE SXF180")
        assert result == "SXF180"

    def test_confusion_swap_O_to_0(self):
        result = _extract_plate_from_text("plate ABC12O")
        assert result == "ABC120"

    def test_confusion_swap_I_to_1(self):
        result = _extract_plate_from_text("I23ABC")
        assert result == "123ABC"

    def test_no_confusion_swap_when_direct_match(self):
        """When a candidate already matches a format, no swap needed."""
        result = _extract_plate_from_text("ABC123")
        assert result == "ABC123"

    def test_slowly_spelled_plate_in_sentence(self):
        """Full pipeline: noise words + spelled letters + digits."""
        result = _extract_plate_from_text("look up the plate S X F 1 8 0")
        assert result == "SXF180"


# ---------------------------------------------------------------------------
# _normalize_words
# ---------------------------------------------------------------------------


class TestNormalizeWords:
    def test_nato_always_applied(self):
        words = ["ALPHA", "BRAVO", "CHARLIE"]
        assert _normalize_words(words) == ["A", "B", "C"]

    def test_number_words_adjacent_to_single_char(self):
        """Number words next to single-char tokens get normalized."""
        words = ["ONE", "TWO", "THREE", "A", "B", "C"]
        result = _normalize_words(words)
        assert result == ["1", "2", "3", "A", "B", "C"]

    def test_number_words_not_adjacent_to_single_char(self):
        """Number words NOT next to single-char tokens stay as-is."""
        words = ["ONE", "CAR", "PLATE", "ABC123"]
        result = _normalize_words(words)
        assert result == ["ONE", "CAR", "PLATE", "ABC123"]

    def test_nato_and_numbers_combined(self):
        words = ["SIERRA", "XRAY", "FOXTROT", "ONE", "EIGHT", "ZERO"]
        result = _normalize_words(words)
        assert result == ["S", "X", "F", "1", "8", "0"]

    def test_forward_backward_propagation(self):
        """Adjacency propagates through chains in both directions."""
        # "ONE TWO THREE A" → NATO makes A single → backward: THREE→3, TWO→2, ONE→1
        words = ["ONE", "TWO", "THREE", "A"]
        result = _normalize_words(words)
        assert result == ["1", "2", "3", "A"]

    def test_forward_propagation_from_leading_single_char(self):
        """Forward pass propagates rightward from a leading single char."""
        words = ["A", "ONE", "TWO", "THREE"]
        result = _normalize_words(words)
        assert result == ["A", "1", "2", "3"]

    def test_no_words_to_normalize(self):
        words = ["ABC", "123"]
        assert _normalize_words(words) == ["ABC", "123"]

    def test_empty_list(self):
        assert _normalize_words([]) == []


# ---------------------------------------------------------------------------
# _merge_single_chars
# ---------------------------------------------------------------------------


class TestMergeSingleChars:
    def test_all_single_chars(self):
        assert _merge_single_chars(["S", "X", "F", "1", "8", "0"]) == ["SXF180"]

    def test_multi_char_breaks_merge(self):
        assert _merge_single_chars(["I", "SEE", "S", "X", "F"]) == [
            "I",
            "SEE",
            "SXF",
        ]

    def test_no_single_chars(self):
        assert _merge_single_chars(["ABC", "123"]) == ["ABC", "123"]

    def test_empty_list(self):
        assert _merge_single_chars([]) == []

    def test_mixed_runs(self):
        assert _merge_single_chars(["A", "B", "SEE", "1", "2"]) == [
            "AB",
            "SEE",
            "12",
        ]


# ---------------------------------------------------------------------------
# _confusion_variants
# ---------------------------------------------------------------------------


class TestConfusionVariants:
    def test_O_to_0(self):
        """ABC12O doesn't match a format, but ABC120 does."""
        variants = _confusion_variants("ABC12O")
        assert "ABC120" in variants

    def test_I_to_1(self):
        variants = _confusion_variants("I23ABC")
        assert "123ABC" in variants

    def test_no_confusables(self):
        assert _confusion_variants("ABC234") == []

    def test_only_format_matching_variants_returned(self):
        """Variants that don't match a plate format are excluded."""
        variants = _confusion_variants("OO")
        # OO→00, O0, 0O — none of these 2-char strings match any plate format
        for v in variants:
            assert v != "OO"  # original is never included
            assert _matches_plate_format(v)

    def test_many_confusable_positions_returns_empty(self):
        """More than 4 confusable positions triggers the guard and returns []."""
        assert _confusion_variants("OIOIO") == []


class TestLoadNoiseWords:
    def test_noise_words_loaded_from_file(self):
        words = _load_noise_words()
        assert isinstance(words, frozenset)
        assert "CAR" in words
        assert "THE" in words
        assert "PLATE" in words
        assert len(words) > 10

    def test_noise_words_missing_file(self, tmp_path, monkeypatch):
        """When noise_words.txt is missing, returns empty frozenset."""
        import stt

        monkeypatch.setattr(stt, "__file__", str(tmp_path / "stt.py"))
        result = _load_noise_words()
        assert result == frozenset()

    def test_noise_words_unreadable_file(self, tmp_path, monkeypatch):
        """When noise_words.txt exists but is unreadable, returns empty frozenset."""
        import stt

        bad_file = tmp_path / "noise_words.txt"
        bad_file.write_bytes(b"\x80\x81\x82")  # invalid UTF-8
        monkeypatch.setattr(stt, "__file__", str(tmp_path / "stt.py"))
        result = _load_noise_words()
        assert result == frozenset()


# ---------------------------------------------------------------------------
# _score_candidate
# ---------------------------------------------------------------------------


class TestScoreCandidate:
    def test_format_match_beats_longer_nonformat(self):
        score_format = _score_candidate("ABC123", 1, True)
        score_longer = _score_candidate("XYABC12", 2, True)
        assert score_format > score_longer

    def test_mixed_beats_letters_only(self):
        score_mixed = _score_candidate("ABC123", 1, True)
        score_alpha = _score_candidate("ABCDEF", 1, True)
        assert score_mixed > score_alpha

    def test_noise_free_beats_non_noise_free(self):
        score_clean = _score_candidate("ABC123", 1, True)
        score_noisy = _score_candidate("ABC123", 1, False)
        assert score_clean > score_noisy

    def test_fewer_confusables_wins_tiebreaker(self):
        score_120 = _score_candidate("ABC120", 1, True)
        score_12O = _score_candidate("ABC12O", 1, True)
        assert score_120 > score_12O

    def test_longer_beats_shorter_same_criteria(self):
        score_long = _score_candidate("ABCD12", 1, True)
        score_short = _score_candidate("ABC12", 1, True)
        assert score_long > score_short


# ---------------------------------------------------------------------------
# _matches_plate_format
# ---------------------------------------------------------------------------


class TestMatchesPlateFormat:
    @pytest.mark.parametrize(
        "plate,expected",
        [
            ("ABC123", True),  # [A-Z]{2,3}[0-9]{3,4}
            ("ABC1234", True),  # [A-Z]{2,3}[0-9]{3,4}
            ("123ABC", True),  # [0-9]{1,4}[A-Z]{2,4}
            ("1ABC234", True),  # California
            ("ABC12D", True),  # Arkansas
            ("123ABC4", True),  # Kansas
            ("1A2345B", True),  # Alabama
            ("A12BC", True),  # Various
            ("123456", True),  # Delaware all-digit (6)
            ("AB12", False),  # Too short for any pattern
            ("ABCDEFGH", False),  # All letters, 8 chars
            ("12345678", False),  # 8 digits exceeds [0-9]{5,7}
            ("A1", False),  # 2 chars, no pattern
        ],
    )
    def test_format_matching(self, plate, expected):
        assert _matches_plate_format(plate) is expected


# ---------------------------------------------------------------------------
# extract_plate_from_voice
# ---------------------------------------------------------------------------


class TestExtractPlateFromVoice:
    @patch("stt._get_model")
    async def test_whisper_params(self, mock_get_model):
        """Verify transcribe is called with language, initial_prompt, and condition_on_previous_text."""
        mock_model = MagicMock()
        seg = MagicMock()
        seg.text = "ABC 1234"
        mock_model.transcribe.return_value = ([seg], MagicMock())
        mock_get_model.return_value = mock_model

        audio_b64 = base64.b64encode(b"fake audio data").decode()
        await extract_plate_from_voice(audio_b64)

        call_kwargs = mock_model.transcribe.call_args
        assert call_kwargs[1]["language"] == "en"
        assert "initial_prompt" in call_kwargs[1]
        assert call_kwargs[1]["condition_on_previous_text"] is False
        assert call_kwargs[1]["beam_size"] == 1

    @patch("stt._get_model")
    async def test_successful_transcription(self, mock_get_model):
        mock_model = MagicMock()
        seg = MagicMock()
        seg.text = "ABC 1234"
        mock_model.transcribe.return_value = ([seg], MagicMock())
        mock_get_model.return_value = mock_model

        audio_b64 = base64.b64encode(b"fake audio data").decode()
        result = await extract_plate_from_voice(audio_b64)
        assert result == "ABC1234"

    @patch("stt._get_model")
    async def test_no_plate_in_speech(self, mock_get_model):
        mock_model = MagicMock()
        seg = MagicMock()
        seg.text = "um uh the a"
        mock_model.transcribe.return_value = ([seg], MagicMock())
        mock_get_model.return_value = mock_model

        audio_b64 = base64.b64encode(b"fake audio data").decode()
        with pytest.raises(STTError, match="No plate number found"):
            await extract_plate_from_voice(audio_b64)

    @patch("stt._get_model")
    async def test_empty_transcription(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([], MagicMock())
        mock_get_model.return_value = mock_model

        audio_b64 = base64.b64encode(b"fake audio data").decode()
        with pytest.raises(STTError, match="Could not transcribe"):
            await extract_plate_from_voice(audio_b64)

    async def test_invalid_base64_raises(self):
        with pytest.raises(STTError, match="Could not decode"):
            await extract_plate_from_voice("not-valid-base64!!!")

    async def test_empty_audio_raises(self):
        empty_b64 = base64.b64encode(b"").decode()
        with pytest.raises(STTError, match="empty"):
            await extract_plate_from_voice(empty_b64)

    @patch("stt._get_model")
    async def test_model_failure_raises(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("Model crashed")
        mock_get_model.return_value = mock_model

        audio_b64 = base64.b64encode(b"fake audio data").decode()
        with pytest.raises(STTError, match="Voice processing failed"):
            await extract_plate_from_voice(audio_b64)

    @patch("stt._get_model")
    async def test_timeout_raises(self, mock_get_model):
        import time

        mock_model = MagicMock()

        def slow_transcribe(*args, **kwargs):
            time.sleep(5)
            return ([], MagicMock())

        mock_model.transcribe.side_effect = slow_transcribe
        mock_get_model.return_value = mock_model

        audio_b64 = base64.b64encode(b"fake audio data").decode()
        with patch("stt._STT_TIMEOUT", 0.1), pytest.raises(STTError, match="timed out"):
            await extract_plate_from_voice(audio_b64)

    @patch("stt._get_model")
    async def test_multiple_segments_concatenated(self, mock_get_model):
        mock_model = MagicMock()
        seg1 = MagicMock()
        seg1.text = "the plate is"
        seg2 = MagicMock()
        seg2.text = "ABC 1234"
        mock_model.transcribe.return_value = ([seg1, seg2], MagicMock())
        mock_get_model.return_value = mock_model

        audio_b64 = base64.b64encode(b"fake audio data").decode()
        result = await extract_plate_from_voice(audio_b64)
        assert result == "ABC1234"

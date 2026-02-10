"""Speech-to-text extraction of license plate numbers from voice messages."""

import asyncio
import base64
import binascii
import logging
import pathlib
import re
import tempfile

logger = logging.getLogger(__name__)

_CLEAN_RE = re.compile(r"[^A-Z0-9]")
_HAS_LETTER = re.compile(r"[A-Z]")
_HAS_DIGIT = re.compile(r"[0-9]")

_NATO_MAP: dict[str, str] = {
    "ALPHA": "A",
    "BRAVO": "B",
    "CHARLIE": "C",
    "DELTA": "D",
    "ECHO": "E",
    "FOXTROT": "F",
    "GOLF": "G",
    "HOTEL": "H",
    "INDIA": "I",
    "JULIET": "J",
    "KILO": "K",
    "LIMA": "L",
    "MIKE": "M",
    "NOVEMBER": "N",
    "OSCAR": "O",
    "PAPA": "P",
    "QUEBEC": "Q",
    "ROMEO": "R",
    "SIERRA": "S",
    "TANGO": "T",
    "UNIFORM": "U",
    "VICTOR": "V",
    "WHISKEY": "W",
    "XRAY": "X",
    "YANKEE": "Y",
    "ZULU": "Z",
}

_NUMBER_MAP: dict[str, str] = {
    "ONE": "1",
    "TWO": "2",
    "THREE": "3",
    "FOUR": "4",
    "FIVE": "5",
    "SIX": "6",
    "SEVEN": "7",
    "EIGHT": "8",
    "NINE": "9",
    "ZERO": "0",
}

_CONFUSABLES: dict[str, str] = {"O": "0", "0": "O", "I": "1", "1": "I"}

# Known US state license plate format patterns.
_US_PLATE_FORMATS = [
    re.compile(r"^[A-Z]{2,3}[0-9]{3,4}$"),  # ABC123, ABC1234 (most states)
    re.compile(r"^[0-9]{1,4}[A-Z]{2,4}$"),  # 123ABC, 1234AB (CT, NV, ME)
    re.compile(r"^[0-9][A-Z]{3}[0-9]{3}$"),  # 1ABC234 (California)
    re.compile(r"^[A-Z]{3}[0-9]{2}[A-Z]$"),  # ABC12D (Arkansas)
    re.compile(r"^[0-9]{3}[A-Z]{3}[0-9]$"),  # 123ABC4 (Kansas)
    re.compile(r"^[0-9]{1,2}[A-Z][0-9]{3,4}[A-Z]$"),  # 1A2345B (Alabama)
    re.compile(r"^[A-Z][0-9]{2,4}[A-Z]{1,3}$"),  # A12BC (various)
    re.compile(r"^[0-9]{5,7}$"),  # 123456 (Delaware, all-digit)
]

_STT_TIMEOUT = 15  # seconds

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel("base", device="cpu", compute_type="int8")
    return _model


class STTError(Exception):
    """Raised when speech-to-text fails or produces no usable plate text."""


def _load_noise_words() -> frozenset[str]:
    """Load noise words from noise_words.txt next to this module.

    Returns an empty frozenset (with a warning) if the file is missing or
    unreadable.
    """
    path = pathlib.Path(__file__).parent / "noise_words.txt"
    try:
        text = path.read_text()
    except FileNotFoundError:
        logger.warning("noise_words.txt not found at %s; using empty set", path)
        return frozenset()
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(
            "Failed to read noise_words.txt at %s (%s: %s); using empty set",
            path,
            type(exc).__name__,
            exc,
        )
        return frozenset()
    words: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        words.append(line.upper())
    return frozenset(words)


_NOISE_WORDS = _load_noise_words()


def _normalize_words(words: list[str]) -> list[str]:
    """Apply NATO alphabet and adjacency-gated number word normalization.

    NATO words are always replaced.  Number words (ONE→1, etc.) are only
    replaced when adjacent to a single-character token, using a two-pass
    (forward then backward) sweep so adjacency propagates through chains
    like "ONE TWO THREE A" → "1 2 3 A".
    """
    # NATO pass — always applied
    result = [_NATO_MAP.get(w, w) for w in words]

    # Number-word pass — adjacency-gated (forward then backward)
    for direction in (range(len(result)), reversed(range(len(result)))):
        for i in direction:
            if result[i] in _NUMBER_MAP:
                prev_single = i > 0 and len(result[i - 1]) == 1
                next_single = i < len(result) - 1 and len(result[i + 1]) == 1
                if prev_single or next_single:
                    result[i] = _NUMBER_MAP[result[i]]
    return result


def _merge_single_chars(words: list[str]) -> list[str]:
    """Collapse consecutive single-character tokens into one token.

    "S", "X", "F", "1", "8", "0" → "SXF180"
    Multi-char tokens break the run.
    """
    merged: list[str] = []
    buf: list[str] = []
    for w in words:
        if len(w) == 1:
            buf.append(w)
        else:
            if buf:
                merged.append("".join(buf))
                buf = []
            merged.append(w)
    if buf:
        merged.append("".join(buf))
    return merged


def _confusion_variants(candidate: str) -> list[str]:
    """Generate O↔0 and I↔1 swap variants that match a US plate format.

    Uses bitmask enumeration over confusable positions.  Returns only
    variants that pass ``_matches_plate_format`` (excludes the original).
    """
    positions = [i for i, ch in enumerate(candidate) if ch in _CONFUSABLES]
    if not positions:
        return []
    if len(positions) > 4:
        return []
    variants: list[str] = []
    for mask in range(1, 1 << len(positions)):
        chars = list(candidate)
        for bit, pos in enumerate(positions):
            if mask & (1 << bit):
                chars[pos] = _CONFUSABLES[chars[pos]]
        v = "".join(chars)
        if v != candidate and _matches_plate_format(v):
            variants.append(v)
    return variants


def _transcribe(audio_bytes: bytes) -> str:
    """Write audio bytes to a temp file and transcribe with faster-whisper."""
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=True) as f:
        f.write(audio_bytes)
        f.flush()
        segments, _info = _get_model().transcribe(
            f.name,
            beam_size=1,
            language="en",
            initial_prompt=("License plate numbers: ABC1234. A B C 1 2 3 4. Alpha Bravo Charlie."),
            condition_on_previous_text=False,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
    if not text:
        raise STTError("Could not transcribe any speech from the voice message.")
    logger.debug("STT transcription: %r", text)
    return text


def _matches_plate_format(candidate: str) -> bool:
    return any(p.fullmatch(candidate) for p in _US_PLATE_FORMATS)


def _score_candidate(candidate: str, group_size: int, noise_free: bool) -> tuple:
    """Score a plate candidate for selection.

    Returns a tuple used for max() comparison:
      (matches_format, is_mixed, noise_free, length,
       -confusable_letters, -group_size)

    The confusable_letters penalty (fewer O/I letters = better) acts as a
    tiebreaker so that confusion-variant "ABC120" beats the original "ABC12O"
    when both match a plate format.
    """
    matches_format = _matches_plate_format(candidate)
    is_mixed = bool(_HAS_LETTER.search(candidate) and _HAS_DIGIT.search(candidate))
    confusable_letters = sum(1 for ch in candidate if ch in ("O", "I"))
    return (
        matches_format,
        is_mixed,
        noise_free,
        len(candidate),
        -confusable_letters,
        -group_size,
    )


def _extract_plate_from_text(text: str) -> str:
    """Extract a license plate number from transcribed text.

    Pipeline: clean → normalize (NATO + number words) → merge single chars
    → noise filter → two-pass candidate generation with confusion variants
    → score and select best.
    """
    all_words = text.upper().split()
    if not all_words:
        raise STTError("Transcription was empty.")

    cleaned_all = [c for c in (_CLEAN_RE.sub("", w) for w in all_words) if c]

    # Normalize NATO + number words, then merge single chars
    normalized = _normalize_words(cleaned_all)
    normalized = _merge_single_chars(normalized)
    logger.debug("STT normalized+merged: %s", normalized)

    filtered = [w for w in normalized if w not in _NOISE_WORDS]
    if not filtered:
        raise STTError(f"No plate number found in transcription: {text!r}")

    # (candidate_str, group_size, noise_free)
    candidates: list[tuple[str, int, bool]] = []

    def _add_with_variants(cand: str, group_size: int, noise_free: bool) -> None:
        candidates.append((cand, group_size, noise_free))
        for variant in _confusion_variants(cand):
            candidates.append((variant, group_size, noise_free))

    # Pass 1: candidates from noise-filtered words (preferred)
    max_group = min(len(filtered), 4)
    for group_size in range(1, max_group + 1):
        for i in range(len(filtered) - group_size + 1):
            cleaned = "".join(filtered[i : i + group_size])
            if 2 <= len(cleaned) <= 8:
                _add_with_variants(cleaned, group_size, True)

    # Pass 2: candidates from all words (post-normalize/merge), but only
    # if they match a US plate format.  Rescues plates whose letters
    # overlap with noise words (e.g. "OF 1234" → OF1234).
    max_group_all = min(len(normalized), 4)
    for group_size in range(1, max_group_all + 1):
        for i in range(len(normalized) - group_size + 1):
            cleaned = "".join(normalized[i : i + group_size])
            if 2 <= len(cleaned) <= 8 and _matches_plate_format(cleaned):
                candidates.append((cleaned, group_size, False))
            elif 2 <= len(cleaned) <= 8:
                for variant in _confusion_variants(cleaned):
                    candidates.append((variant, group_size, False))

    if not candidates:
        raise STTError(f"No plate number found in transcription: {text!r}")

    best = max(candidates, key=lambda c: _score_candidate(c[0], c[1], c[2]))
    logger.debug(
        "STT candidates top-3: %s",
        sorted(
            {c[0] for c in candidates},
            key=lambda x: _score_candidate(x, 1, True),
            reverse=True,
        )[:3],
    )
    logger.debug("STT selected plate: %s", best[0])
    return best[0]


async def extract_plate_from_voice(base64_data: str) -> str:
    """Decode, transcribe, and extract a plate number from a base64 voice message.

    Returns the extracted plate string (A-Z0-9 only, 2-8 chars).
    Raises STTError on failure.
    """
    try:
        audio_bytes = base64.b64decode(base64_data)
    except (binascii.Error, ValueError) as exc:
        raise STTError("Could not decode voice message attachment.") from exc

    if not audio_bytes:
        raise STTError("Voice message attachment is empty.")

    def _run():
        text = _transcribe(audio_bytes)
        return _extract_plate_from_text(text)

    try:
        plate = await asyncio.wait_for(
            asyncio.to_thread(_run),
            timeout=_STT_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        raise STTError("Voice transcription timed out.") from exc
    except STTError:
        raise
    except Exception as exc:
        logger.warning("STT processing failed: %s: %s", type(exc).__name__, exc)
        raise STTError(f"Voice processing failed: {exc}") from exc
    return plate

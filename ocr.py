"""License plate OCR extraction from images using fast-alpr."""

import asyncio
import base64
import logging
import re

import cv2
import numpy as np
from fast_alpr import ALPR

logger = logging.getLogger(__name__)

_PLATE_RE = re.compile(r"[A-Z0-9]{2,8}")

_MAX_IMAGE_PIXELS = 25_000_000  # ~25MP

_ALPR_TIMEOUT = 15  # seconds

_alpr: ALPR | None = None


def _get_alpr() -> ALPR:
    global _alpr
    if _alpr is None:
        _alpr = ALPR(
            detector_model="yolo-v9-t-384-license-plate-end2end",
            ocr_model="cct-xs-v1-global-model",
        )
    return _alpr


class OCRError(Exception):
    """Raised when OCR fails or produces no usable plate text."""


def decode_image(base64_data: str) -> np.ndarray:
    """Decode a base64-encoded image string into a BGR numpy array."""
    image_bytes = base64.b64decode(base64_data)
    buf = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if frame is None:
        raise OCRError("Attachment is not a recognized image format.")
    h, w = frame.shape[:2]
    if w * h > _MAX_IMAGE_PIXELS:
        raise OCRError("Image is too large to process.")
    return frame


def _extract_plate_text(frame: np.ndarray) -> str:
    """Run ALPR on a frame and return the best plate text.

    Picks the result with the highest OCR confidence, cleans text
    to A-Z0-9 only, and validates 2-8 characters.
    """
    results = _get_alpr().predict(frame)
    if not results:
        raise OCRError("Could not detect any license plate in the image.")

    # Filter to results that have OCR text
    with_ocr = [r for r in results if r.ocr is not None]
    if not with_ocr:
        raise OCRError("Detected a plate region but could not read the text.")

    # Pick highest OCR confidence
    def _avg_confidence(r):
        conf = r.ocr.confidence
        if isinstance(conf, float):
            return conf
        return sum(conf) / len(conf) if conf else 0.0

    best = max(with_ocr, key=_avg_confidence)
    raw = best.ocr.text.upper()
    cleaned = re.sub(r"[^A-Z0-9]", "", raw)

    match = _PLATE_RE.search(cleaned)
    if not match:
        raise OCRError("Could not read any text from the image.")
    return match.group()


async def extract_plate_from_image(base64_data: str) -> str:
    """Decode, detect, and OCR a base64 image to extract a plate number.

    Returns the extracted plate string (A-Z0-9 only, 2-8 chars).
    Raises OCRError on failure.
    """
    try:
        frame = decode_image(base64_data)
    except OCRError:
        raise
    except Exception as exc:
        logger.warning("Image decode failed: %s: %s", type(exc).__name__, exc)
        raise OCRError("Attachment is not a recognized image format.") from exc
    try:
        plate = await asyncio.wait_for(
            asyncio.to_thread(_extract_plate_text, frame),
            timeout=_ALPR_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise OCRError("Plate detection timed out. Try a clearer or smaller image.")
    except OCRError:
        raise
    except Exception as exc:
        logger.warning("OCR processing failed: %s: %s", type(exc).__name__, exc)
        raise OCRError(f"OCR processing failed: {exc}") from exc
    return plate

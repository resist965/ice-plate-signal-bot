import asyncio
import base64
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from ocr import (
    OCRError,
    _MAX_IMAGE_PIXELS,
    decode_image,
    _extract_plate_text,
    extract_plate_from_image,
)


def _make_alpr_result(ocr_text=None, ocr_confidence=0.9, det_confidence=0.95):
    """Create a mock ALPRResult with the given OCR text and confidence."""
    result = MagicMock()
    result.detection.confidence = det_confidence
    if ocr_text is not None:
        result.ocr.text = ocr_text
        result.ocr.confidence = ocr_confidence
    else:
        result.ocr = None
    return result


class TestDecodeImage:
    def test_valid_png(self, plate_image_base64):
        frame = decode_image(plate_image_base64)
        assert isinstance(frame, np.ndarray)
        assert frame.shape == (60, 200, 3)  # height, width, channels

    def test_valid_jpeg(self):
        import cv2
        img = np.full((50, 100, 3), (0, 0, 255), dtype=np.uint8)
        _, buf = cv2.imencode(".jpg", img)
        b64 = base64.b64encode(buf).decode()

        result = decode_image(b64)
        assert isinstance(result, np.ndarray)

    def test_invalid_base64_raises(self):
        with pytest.raises(Exception):
            decode_image("not-valid-base64!!!")

    def test_too_large_image_raises(self):
        with patch("ocr.cv2.imdecode") as mock_decode:
            mock_frame = np.zeros((5000, 6000, 3), dtype=np.uint8)
            mock_decode.return_value = mock_frame
            b64 = base64.b64encode(b"\x00" * 10).decode()
            with pytest.raises(OCRError, match="too large"):
                decode_image(b64)

    def test_non_image_data_raises(self):
        b64 = base64.b64encode(b"this is not an image").decode()
        with pytest.raises(OCRError, match="not a recognized image format"):
            decode_image(b64)


class TestExtractPlateText:
    @patch("ocr._get_alpr")
    def test_single_plate_detected(self, mock_get_alpr):
        mock_alpr = MagicMock()
        mock_alpr.predict.return_value = [_make_alpr_result("ABC1234")]
        mock_get_alpr.return_value = mock_alpr

        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        assert _extract_plate_text(frame) == "ABC1234"

    @patch("ocr._get_alpr")
    def test_multiple_plates_picks_highest_confidence(self, mock_get_alpr):
        mock_alpr = MagicMock()
        mock_alpr.predict.return_value = [
            _make_alpr_result("LOW1234", ocr_confidence=0.5),
            _make_alpr_result("HIGH567", ocr_confidence=0.95),
        ]
        mock_get_alpr.return_value = mock_alpr

        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        assert _extract_plate_text(frame) == "HIGH567"

    @patch("ocr._get_alpr")
    def test_mixed_ocr_none_and_valid(self, mock_get_alpr):
        mock_alpr = MagicMock()
        mock_alpr.predict.return_value = [
            _make_alpr_result(ocr_text=None),
            _make_alpr_result("GOOD123", ocr_confidence=0.9),
        ]
        mock_get_alpr.return_value = mock_alpr

        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        assert _extract_plate_text(frame) == "GOOD123"

    @patch("ocr._get_alpr")
    def test_no_plates_detected_raises(self, mock_get_alpr):
        mock_alpr = MagicMock()
        mock_alpr.predict.return_value = []
        mock_get_alpr.return_value = mock_alpr

        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        with pytest.raises(OCRError, match="Could not detect any license plate"):
            _extract_plate_text(frame)

    @patch("ocr._get_alpr")
    def test_plate_detected_but_ocr_none_raises(self, mock_get_alpr):
        mock_alpr = MagicMock()
        mock_alpr.predict.return_value = [_make_alpr_result(ocr_text=None)]
        mock_get_alpr.return_value = mock_alpr

        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        with pytest.raises(OCRError, match="could not read the text"):
            _extract_plate_text(frame)

    @patch("ocr._get_alpr")
    def test_ocr_text_cleaned_to_alphanumeric(self, mock_get_alpr):
        mock_alpr = MagicMock()
        mock_alpr.predict.return_value = [_make_alpr_result("AB-C 12.34")]
        mock_get_alpr.return_value = mock_alpr

        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        assert _extract_plate_text(frame) == "ABC1234"

    @patch("ocr._get_alpr")
    def test_ocr_text_too_short_raises(self, mock_get_alpr):
        mock_alpr = MagicMock()
        mock_alpr.predict.return_value = [_make_alpr_result("A")]
        mock_get_alpr.return_value = mock_alpr

        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        with pytest.raises(OCRError, match="Could not read any text"):
            _extract_plate_text(frame)

    @patch("ocr._get_alpr")
    def test_confidence_as_list(self, mock_get_alpr):
        """OcrResult.confidence can be a list of per-char confidences."""
        mock_alpr = MagicMock()
        r1 = _make_alpr_result("LOW1234", ocr_confidence=[0.3, 0.4, 0.5])
        r2 = _make_alpr_result("HIGH567", ocr_confidence=[0.9, 0.95, 0.92])
        mock_alpr.predict.return_value = [r1, r2]
        mock_get_alpr.return_value = mock_alpr

        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        assert _extract_plate_text(frame) == "HIGH567"

    @patch("ocr._get_alpr")
    def test_empty_confidence_list_does_not_crash(self, mock_get_alpr):
        mock_alpr = MagicMock()
        mock_alpr.predict.return_value = [
            _make_alpr_result("ABC123", ocr_confidence=[]),
        ]
        mock_get_alpr.return_value = mock_alpr

        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        assert _extract_plate_text(frame) == "ABC123"


class TestExtractPlateFromImage:
    @patch("ocr._get_alpr")
    async def test_full_pipeline(self, mock_get_alpr, plate_image_base64):
        mock_alpr = MagicMock()
        mock_alpr.predict.return_value = [_make_alpr_result("ABC1234")]
        mock_get_alpr.return_value = mock_alpr

        result = await extract_plate_from_image(plate_image_base64)
        assert result == "ABC1234"

    @patch("ocr._get_alpr")
    async def test_alpr_failure_raises(self, mock_get_alpr, plate_image_base64):
        mock_alpr = MagicMock()
        mock_alpr.predict.side_effect = RuntimeError("Model failed")
        mock_get_alpr.return_value = mock_alpr

        with pytest.raises(OCRError, match="OCR processing failed"):
            await extract_plate_from_image(plate_image_base64)

    async def test_non_image_raises(self):
        garbage = base64.b64encode(b"this is not an image").decode()
        with pytest.raises(OCRError, match="not a recognized image format"):
            await extract_plate_from_image(garbage)

    async def test_decode_failure_raises_ocr_error(self):
        with pytest.raises(OCRError, match="not a recognized image format"):
            await extract_plate_from_image("not-valid-base64!!!")

    @patch("ocr._get_alpr")
    async def test_no_detection_raises(self, mock_get_alpr, plate_image_base64):
        mock_alpr = MagicMock()
        mock_alpr.predict.return_value = []
        mock_get_alpr.return_value = mock_alpr

        with pytest.raises(OCRError, match="Could not detect"):
            await extract_plate_from_image(plate_image_base64)

    @patch("ocr._get_alpr")
    async def test_timeout_raises_ocr_error(self, mock_get_alpr, plate_image_base64):
        mock_alpr = MagicMock()

        def slow_predict(_frame):
            import time
            time.sleep(5)
            return []

        mock_alpr.predict.side_effect = slow_predict
        mock_get_alpr.return_value = mock_alpr

        with patch("ocr._ALPR_TIMEOUT", 0.1):
            with pytest.raises(OCRError, match="timed out"):
                await extract_plate_from_image(plate_image_base64)

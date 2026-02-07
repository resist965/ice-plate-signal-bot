import base64
import hashlib
import json
import os
import pathlib
from unittest.mock import AsyncMock, MagicMock

import cv2
import numpy as np
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import lookup
import lookup_defrost

SNAPSHOT_DIR = pathlib.Path(__file__).resolve().parent.parent / "html_snapshots"


@pytest.fixture(scope="session")
def html_search_match():
    return (SNAPSHOT_DIR / "search_match.html").read_text()


@pytest.fixture(scope="session")
def html_search_no_match():
    return (SNAPSHOT_DIR / "search_no_match.html").read_text()


@pytest.fixture(scope="session")
def html_detail_page():
    return (SNAPSHOT_DIR / "detail_page.html").read_text()


@pytest.fixture(scope="session")
def defrost_json_sample():
    return (SNAPSHOT_DIR / "defrost_sample.json").read_text()


@pytest.fixture(scope="session")
def defrost_page_sample():
    return (SNAPSHOT_DIR / "defrost_page_sample.json").read_text()


@pytest.fixture(autouse=True)
def reset_lookup_session():
    yield
    lookup._session = None


@pytest.fixture(autouse=True)
def reset_defrost_caches():
    yield
    lookup_defrost.clear_caches()


@pytest.fixture
def mock_context():
    def _factory(text="", reaction=None, raw_message=None, base64_attachments=None):
        ctx = MagicMock()
        ctx.send = AsyncMock()
        ctx.reply = AsyncMock(return_value=1234567890)
        ctx.react = AsyncMock()
        ctx.message.text = text
        ctx.message.reaction = reaction
        ctx.message.raw_message = raw_message
        ctx.message.base64_attachments = base64_attachments or []
        return ctx

    return _factory


_TEST_PASSWORD = "test-password-123"


def _encrypt_data(plaintext_str: str, password: str) -> dict:
    """Encrypt data using the same AES-256-GCM scheme as defrostmn.net."""
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000, dklen=32)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(iv, plaintext_str.encode(), None)
    return {
        "salt": base64.b64encode(salt).decode(),
        "iv": base64.b64encode(iv).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }


@pytest.fixture(scope="session")
def defrost_encrypted_page():
    """Create an encrypted test page with known password and plaintext for roundtrip testing."""
    plaintext_data = {
        "records": [
            {
                "id": "recTEST1",
                "createdTime": "2026-01-15T12:00:00.000Z",
                "fields": {
                    "Plate ID": "TEST123 (MN)",
                    "Plate": "TEST123",
                    "Reports Count": 3,
                    "Plate Issuer": "MN - Minnesota",
                    "Tags": "ICE decals/insignia",
                    "Unique vehicles": "White Honda Civic",
                    "Plate Status": ["Confirmed ICE"],
                    "Last Seen": "2026-01-27T19:30:00.000Z",
                    "Last Location Seen": "123 Main St, Minneapolis",
                    "First seen": "2026-01-15T06:00:00.000Z",
                    "Vehicle Description": "White Honda Civic",
                },
            }
        ],
        "offset": "itrTEST/recNext",
        "updated": "2026-02-01T12:00:00.000Z",
    }
    plaintext_str = json.dumps(plaintext_data)
    encrypted = _encrypt_data(plaintext_str, _TEST_PASSWORD)
    return {
        "password": _TEST_PASSWORD,
        "plaintext_data": plaintext_data,
        "plaintext_str": plaintext_str,
        "encrypted": encrypted,
    }


def _make_image_base64(text=None, size=(200, 60)):
    """Create a synthetic image and return its base64 encoding."""
    img = np.full((size[1], size[0], 3), 255, dtype=np.uint8)
    if text:
        cv2.putText(img, text, (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    _, buf = cv2.imencode(".png", img)
    return base64.b64encode(buf).decode()


@pytest.fixture(scope="session")
def plate_image_base64():
    """Synthetic license plate image with text 'ABC1234'."""
    return _make_image_base64("ABC1234")

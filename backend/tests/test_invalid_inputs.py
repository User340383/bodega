"""NFR-714: a 30-case set of invalid and malicious inputs sent directly to the backend, bypassing
the interface. Every case must be rejected (400), create no submission, store nothing and make
no AI call. NFR-701 is checked on the same set: each validation result returns within 2.0 s.
"""
import io
import time
from datetime import timedelta

import pytest
from PIL import Image

from app.database import connect
from app.main import app
from app.storage import InMemoryObjectStorage, get_object_storage
from app.vision import MockVisionModel, get_vision_model
from conftest import AS_OF

TODAY = AS_OF.isoformat()


def png(width=8, height=8):
    out = io.BytesIO()
    Image.new('RGB', (width, height), 'white').save(out, format='PNG')
    return out.getvalue()


def huge_png_header():
    """A valid PNG signature and IHDR declaring 50,000 x 50,000 pixels (decompression bomb)."""
    import struct
    import zlib
    ihdr = struct.pack('>IIBBBBB', 50000, 50000, 8, 2, 0, 0, 0)
    chunk = b'IHDR' + ihdr
    return b'\x89PNG\r\n\x1a\n' + struct.pack('>I', len(ihdr)) + chunk + struct.pack('>I', zlib.crc32(chunk))


CASES = [
    # (name, form data, files)
    ('oversized image', {'saleDate': TODAY}, {'file': ('big.jpg', b'\xff\xd8\xff' + b'0' * (10 * 1024 * 1024), 'image/jpeg')}),
    ('renamed text file', {'saleDate': TODAY}, {'file': ('photo.jpg', b'Bread 3 1.50', 'image/jpeg')}),
    ('renamed pdf', {'saleDate': TODAY}, {'file': ('photo.png', b'%PDF-1.7 fake', 'image/png')}),
    ('gif image', {'saleDate': TODAY}, {'file': ('photo.gif', b'GIF89a' + b'0' * 20, 'image/gif')}),
    ('html as image', {'saleDate': TODAY}, {'file': ('x.png', b'<html><script>alert(1)</script>', 'image/png')}),
    ('svg with script', {'saleDate': TODAY}, {'file': ('x.svg', b'<svg onload="alert(1)"/>', 'image/svg+xml')}),
    ('truncated jpeg', {'saleDate': TODAY}, {'file': ('x.jpg', b'\xff\xd8\xff\xe0' + b'\x00' * 10, 'image/jpeg')}),
    ('truncated png', {'saleDate': TODAY}, {'file': ('x.png', png()[:30], 'image/png')}),
    ('decompression bomb', {'saleDate': TODAY}, {'file': ('bomb.png', huge_png_header(), 'image/png')}),
    ('empty file', {'saleDate': TODAY}, {'file': ('x.jpg', b'', 'image/jpeg')}),
    ('script tag text', {'saleDate': TODAY, 'text': '<script>alert(1)</script>'}, None),
    ('img onerror text', {'saleDate': TODAY, 'text': 'Bread 3 <img src=x onerror=alert(1)>'}, None),
    ('javascript url text', {'saleDate': TODAY, 'text': 'javascript:alert(document.cookie)'}, None),
    ('iframe text', {'saleDate': TODAY, 'text': 'Milk 2 <iframe src="https://evil.example">'}, None),
    ('closing tag text', {'saleDate': TODAY, 'text': 'Bread 3 </textarea><b>x'}, None),
    ('nul byte text', {'saleDate': TODAY, 'text': 'Bread\x003 1.50'}, None),
    ('escape sequence text', {'saleDate': TODAY, 'text': 'Bread 3 \x1b[31m1.50'}, None),
    ('blank text', {'saleDate': TODAY, 'text': '   \n\t '}, None),
    ('text over 5000 characters', {'saleDate': TODAY, 'text': 'Bread 3 1.50\n' * 400}, None),
    ('both photo and text', {'saleDate': TODAY, 'text': 'Bread 3'}, {'file': ('x.png', png(), 'image/png')}),
    ('neither photo nor text', {'saleDate': TODAY}, None),
    ('missing date', {'text': 'Bread 3'}, None),
    ('malformed date', {'saleDate': 'not-a-date', 'text': 'Bread 3'}, None),
    ('impossible date', {'saleDate': '2026-02-30', 'text': 'Bread 3'}, None),
    ('month 13', {'saleDate': '2026-13-01', 'text': 'Bread 3'}, None),
    ('sql in date', {'saleDate': "2026-04-30'; DROP TABLE submissions;--", 'text': 'Bread 3'}, None),
    ('script in date', {'saleDate': '<script>alert(1)</script>', 'text': 'Bread 3'}, None),
    ('future date', {'saleDate': (AS_OF + timedelta(days=1)).isoformat(), 'text': 'Bread 3'}, None),
    ('date over a year ago', {'saleDate': (AS_OF - timedelta(days=366)).isoformat(), 'text': 'Bread 3'}, None),
    ('far future date', {'saleDate': '9999-12-31', 'text': 'Bread 3'}, None),
]


def test_set_has_thirty_cases():
    assert len(CASES) == 30 and len({name for name, *_ in CASES}) == 30


@pytest.fixture
def isolated(client):
    model = MockVisionModel()
    storage = InMemoryObjectStorage()
    app.dependency_overrides[get_vision_model] = lambda: model
    app.dependency_overrides[get_object_storage] = lambda: storage
    return client, model, storage


@pytest.mark.parametrize('name,data,files', CASES, ids=[case[0] for case in CASES])
def test_invalid_or_malicious_input_is_rejected_without_side_effects(isolated, name, data, files):
    client, model, storage = isolated
    started = time.monotonic()
    response = client.post('/api/v1/extract', data=data, files=files)
    elapsed = time.monotonic() - started
    assert response.status_code == 400, f'{name}: {response.status_code} {response.text[:200]}'
    body = response.json()
    assert body['error']['code'] == 'VALIDATION_ERROR' and body['error']['message']
    assert '<script' not in response.text and 'DROP TABLE' not in response.text  # Never echoed.
    assert model.calls == [] and storage.objects == {}
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM submissions').fetchone()[0] == 0
    assert elapsed < 2.0, f'NFR-701: validation took {elapsed:.2f} s'


def test_valid_text_still_works_after_the_set(isolated):
    client, model, _ = isolated
    response = client.post('/api/v1/extract', data={'saleDate': TODAY, 'text': 'Bread 3 1.50 & Milk 2 < 3'})
    # "&" and a lone "<" that is not a tag are ordinary characters in a sales log.
    assert response.status_code == 200 and model.calls

"""Security QA (José): EXIF/metadata stripping and contact masking happen before any AI processing.

Pure unit tests: no database, network or paid provider.
"""
import io

import pytest
from PIL import Image

from app import privacy
from app.privacy import InvalidImageError, mask_text, sanitize_image

GPS_IFD = 0x8825
MAKE, MODEL, ORIENTATION, DATETIME = 0x010F, 0x0110, 0x0112, 0x0132


def photo(size=(40, 20), fmt="JPEG", orientation=None, color=(200, 30, 30)):
    """A small synthetic photo carrying GPS, device and time metadata."""
    image = Image.new("RGB", size, color)
    exif = image.getexif()
    exif[MAKE] = "SecretCam"
    exif[MODEL] = "Model-X9"
    exif[DATETIME] = "2026:10:01 08:15:00"
    if orientation:
        exif[ORIENTATION] = orientation
    gps = exif.get_ifd(GPS_IFD)  # ASCII GPS tags: latitude/longitude refs and date stamp.
    gps[1] = "N"
    gps[3] = "W"
    gps[29] = "2026:10:01"
    out = io.BytesIO()
    image.save(out, format=fmt, exif=exif.tobytes())
    return out.getvalue()


# ---------- image sanitization ----------

def test_fixture_really_contains_the_metadata_we_strip():
    original = photo()
    assert b"SecretCam" in original
    with Image.open(io.BytesIO(original)) as image:
        assert image.getexif()[MAKE] == "SecretCam"
        assert image.getexif().get_ifd(GPS_IFD)


def test_sanitize_removes_all_exif_including_gps_and_device():
    clean = sanitize_image(photo())
    assert b"SecretCam" not in clean and b"Model-X9" not in clean and b"2026:10:01" not in clean
    assert b"Exif" not in clean[:4096]
    with Image.open(io.BytesIO(clean)) as image:
        assert image.format == "JPEG"
        assert len(image.getexif()) == 0
        assert not image.getexif().get_ifd(GPS_IFD)
        assert "exif" not in image.info and "icc_profile" not in image.info and "xmp" not in image.info


def test_sanitize_applies_orientation_before_dropping_it():
    clean = sanitize_image(photo(size=(40, 20), orientation=6))  # 6 = rotate 90 degrees
    with Image.open(io.BytesIO(clean)) as image:
        assert image.size == (20, 40)


def test_sanitize_resizes_long_edge_to_2048_and_converts_png_to_jpeg():
    big_png = io.BytesIO()
    Image.new("RGBA", (3000, 1500), (0, 0, 255, 128)).save(big_png, format="PNG")
    clean = sanitize_image(big_png.getvalue())
    with Image.open(io.BytesIO(clean)) as image:
        assert image.format == "JPEG" and image.mode == "RGB"
        assert max(image.size) == 2048 and image.size == (2048, 1024)


def test_sanitize_keeps_small_images_at_original_size():
    with Image.open(io.BytesIO(sanitize_image(photo(size=(640, 480))))) as image:
        assert image.size == (640, 480)


@pytest.mark.parametrize("data", [b"", b"not an image", b"GIF89a....", b"%PDF-1.7"])
def test_non_jpeg_png_bytes_are_rejected_by_magic_bytes(data):
    assert privacy.detect_image_type(data) is None
    with pytest.raises(InvalidImageError):
        sanitize_image(data)


def test_spoofed_content_type_cannot_pass_magic_byte_check():
    assert privacy.detect_image_type(b"\xff\xd8\xff" + b"garbage" * 10) == "image/jpeg"
    with pytest.raises(InvalidImageError):  # Right magic bytes, but not a decodable image.
        sanitize_image(b"\xff\xd8\xff" + b"garbage" * 10)


def test_images_over_10_mib_are_rejected():
    with pytest.raises(InvalidImageError):
        sanitize_image(privacy.JPEG_MAGIC + b"\0" * privacy.MAX_IMAGE_BYTES)


def test_images_over_40_megapixels_are_rejected_without_decoding(monkeypatch):
    monkeypatch.setattr(privacy, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(InvalidImageError, match="megapixels"):
        sanitize_image(photo(size=(20, 20)))


# ---------- contact masking (examples required by Milestone 3, section 9.5) ----------

@pytest.mark.parametrize("phone", [
    "+1 809 555 1234", "(809) 555-1234", "809-555-1234", "809.555.1234",
    "829-555-1234", "849-555-1234", "555-1234", "8095551234", "+18095551234", "+1 (809) 555-1234",
])
def test_phone_numbers_are_masked(phone):
    assert mask_text(f"Call {phone} tomorrow") == "Call [removed] tomorrow"


@pytest.mark.parametrize("email", ["ana@example.com", "j.otazu+shop@mail.example.do", "OWNER_1@Bodega.COM"])
def test_emails_are_masked(email):
    assert mask_text(f"Bread 3 1.50 {email}") == "Bread 3 1.50 [removed]"


@pytest.mark.parametrize("line", [
    "Coke 2 1.50 3.00",       # The earlier generic pattern erased this whole line.
    "Rice (lb) 2.5 0.80 2.00",
    "2026-10-01 sales",       # ISO dates stay intact.
    "Milk 12 25.00 300.00",
    "Eggs 30 x 0.15",
    "Total 1,234.50",
])
def test_ordinary_sales_lines_and_dates_are_untouched(line):
    assert mask_text(line) == line


def test_mixed_log_masks_only_contacts():
    log = "Bread 3 1.50\nAna 809-555-1234 owes\nMilk 2 2.25 ana@example.com\n2026-10-01"
    assert mask_text(log) == "Bread 3 1.50\nAna [removed] owes\nMilk 2 2.25 [removed]\n2026-10-01"

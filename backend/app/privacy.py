"""Privacy controls that run before any input is stored or sent to an AI provider (M3 9.5).

- Image sanitization: apply EXIF orientation, copy pixels only, re-encode as JPEG q85 with the
  long edge at most 2,048 px. All EXIF/XMP/IPTC/ICC metadata (GPS, device, capture time) is
  dropped because the output image is rebuilt from raw pixel bytes.
- Contact masking: phone numbers and emails become "[removed]". The phone pattern is the
  NANP-aware one from Milestone 3, so ordinary sales lines and ISO dates survive.
"""
import io
import re

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MiB
MAX_IMAGE_PIXELS = 40_000_000  # 40 megapixels
MAX_LONG_EDGE = 2048
JPEG_QUALITY = 85
MAX_TEXT_CHARS = 5000

JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG"

MASK = "[removed]"

# Milestone 3, section 9.5. Applied before the email pattern.
PHONE_PATTERN = re.compile(
    r"(?<![\w.,])(?:\+\d{1,3}[\s.-]?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}"
    r"|(?:\(\d{3}\)\s?|\d{3}[.-])\d{3}[.-]\d{4}"
    r"|\d{3}-\d{4}"
    r"|\+?\d{10,15})(?![\w.,])"
)
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class InvalidImageError(ValueError):
    """The upload is not an acceptable JPEG/PNG image. The message is safe to show the owner."""


def detect_image_type(data: bytes) -> str | None:
    """Return 'image/jpeg' or 'image/png' from magic bytes, never from the client's header."""
    if data.startswith(JPEG_MAGIC):
        return "image/jpeg"
    if data.startswith(PNG_MAGIC):
        return "image/png"
    return None


def sanitize_image(data: bytes) -> bytes:
    """Return a metadata-free JPEG copy of an uploaded JPEG/PNG. Raises InvalidImageError."""
    if len(data) > MAX_IMAGE_BYTES:
        raise InvalidImageError("Choose a JPEG or PNG photo under 10 MB.")
    if detect_image_type(data) is None:
        raise InvalidImageError("Choose a JPEG or PNG photo under 10 MB.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
            if width * height > MAX_IMAGE_PIXELS:
                raise InvalidImageError("This photo is too large. Use a photo under 40 megapixels.")
            image = ImageOps.exif_transpose(probe)  # Apply orientation before metadata is dropped.
            image = image.convert("RGB")
    except InvalidImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidImageError("This photo could not be opened. Choose a JPEG or PNG photo.") from exc

    image.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE), Image.Resampling.LANCZOS)
    # Rebuild from pixel bytes only, so no info dict (EXIF, XMP, IPTC, ICC, comments) survives.
    clean = Image.frombytes("RGB", image.size, image.tobytes())
    output = io.BytesIO()
    clean.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return output.getvalue()


def mask_text(text: str) -> str:
    """Replace every phone number and email address with [removed]."""
    return EMAIL_PATTERN.sub(MASK, PHONE_PATTERN.sub(MASK, text))

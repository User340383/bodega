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
# The alternatives are M3's. Its boundaries were (?<![\w.,]) and (?![\w.,]), which also rejected
# ordinary punctuation: "call 555-123-4567, thanks" or a number ending a sentence went to the AI
# unmasked (SRS AC-19). A "." or "," now blocks a match only when a digit follows/precedes it,
# which is what keeps decimals and thousands ("1,250.75", "1234567.89") from being masked.
PHONE_PATTERN = re.compile(
    r"(?<!\w)(?<!\d[.,])(?:\+\d{1,3}[\s.-]?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}"
    r"|(?:\(\d{3}\)\s?|\d{3}[.-])\d{3}[.-]\d{4}"
    r"|\d{3}-\d{4}"
    r"|\+?\d{10,15})(?!\w)(?![.,]\d)"
)
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


# NFR-714: script or markup injection and control characters are rejected server-side.
# Sales logs never need angle-bracket tags; tab, newline and carriage return are allowed.
_MARKUP_TAG = re.compile(r"<\s*/?\s*[A-Za-z!?][^>]*>|<\s*script|javascript\s*:", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def text_problem(text: str) -> str | None:
    """Return a plain-English reason the (already trimmed) sales text is invalid, or None."""
    if not text or len(text) > MAX_TEXT_CHARS:
        return "Text must contain 1-5,000 characters."
    if _CONTROL.search(text):
        return "Text contains characters that are not allowed. Type or paste plain sales lines."
    if _MARKUP_TAG.search(text):
        return "Text cannot contain web code or tags. Type or paste plain sales lines."
    return None


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

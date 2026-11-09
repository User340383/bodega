"""Live check of the real Anthropic integration (FR-201 extraction, FR-501 guidance wording).

    AI_API_KEY=... python scripts/live_ai_check.py        (from backend/, or with PYTHONPATH=backend)

Makes a few billed API calls with synthetic data only (no owner data): one text extraction, one
photo extraction from a generated image of a sales log, and one guidance wording request. It
prints what came back and whether Bodega's own checks accepted it. Without AI_API_KEY it exits
with code 2 and makes no call. Its result is the evidence the traceability matrix asks for; the
automated test suite never calls a paid API.
"""
import io
import json
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main():
    if not os.getenv("AI_API_KEY"):
        print("AI_API_KEY is not set: no call was made. Set an authorized key to run the live check.")
        return 2
    from PIL import Image, ImageDraw

    from app.analysis import build_analysis
    from app.guidance import GuidanceEngine
    from app.ingestion import InferenceService, apply_confidence_bands
    from app.llm import DEFAULT_GUIDANCE_MODEL, ClaudeLLMService
    from app.privacy import mask_text, sanitize_image
    from app.vision import DEFAULT_EXTRACTION_MODEL, EXTRACTION_PROMPT, ClaudeVisionModel

    key = os.environ["AI_API_KEY"]
    vision = ClaudeVisionModel(key, os.getenv("AI_EXTRACTION_MODEL") or DEFAULT_EXTRACTION_MODEL)
    service = InferenceService(vision)
    report = {}

    text = mask_text("Bread 3 1.50\nMilk 2 1.20\nCall Ana 809-555-1234\nRice (lb) 2.5 0.80")
    result = service._call_provider("live-text", lambda: vision.extract_from_text(text, EXTRACTION_PROMPT, 30))
    report["textExtraction"] = None if result is None else apply_confidence_bands(result, "TEXT")

    image = Image.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(image)
    for row, line in enumerate(["Sales 2026-11-05", "Bread   3   1.50", "Milk    2   1.20", "Eggs   12   0.25"]):
        draw.text((60, 60 + row * 90), line, fill="black", font_size=48)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    photo = sanitize_image(buffer.getvalue())
    result = service._call_provider("live-photo", lambda: vision.extract_from_image(photo, EXTRACTION_PROMPT, 30))
    report["photoExtraction"] = None if result is None else apply_confidence_bands(result, "IMAGE")

    end = date.today()
    records = [{"saleDate": end - timedelta(days=i), "itemName": name, "quantity": Decimal(q)}
               for i in range(14) for name, q in (("Bread", "4" if i < 7 else "2"), ("Milk", "2.5"))]
    analysis = build_analysis(records, end, end - timedelta(days=27))
    wording = ClaudeLLMService(key, os.getenv("AI_GUIDANCE_MODEL") or DEFAULT_GUIDANCE_MODEL)
    guidance = GuidanceEngine(wording).generate(analysis)
    report["guidance"] = guidance.model_dump(mode="json")

    print(json.dumps(report, indent=2, default=str))
    ok = (report["textExtraction"] and report["textExtraction"]["status"] == "NEEDS_VERIFICATION"
          and report["photoExtraction"] and report["photoExtraction"]["status"] == "NEEDS_VERIFICATION"
          and guidance.source == "AI_WORDED" and guidance.groundingPassed)
    print("LIVE CHECK:", "PASS" if ok else "REVIEW THE OUTPUT ABOVE (a fallback or rejection occurred)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

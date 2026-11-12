"""Extraction benchmark for NFR-751 and NFR-752 (M3 12.1 "benchmark set of 20 real pages").

    python -m benchmarks.extraction_benchmark <folder> [--provider claude|mock]

<folder> holds JPEG/PNG photos and truth.json:

    {"page01.jpg": {"lines": [{"itemName": "Bread", "quantity": "3", "unitPrice": "1.50"}]},
     "blurry03.jpg": {"unreadable": true}}

Each photo goes through the production path without a database: sanitize_image -> VisionModel
-> apply_confidence_bands. Reported against the SRS targets:
- NFR-751: of fields prefilled without a warning (band HIGH), at least 95% equal the truth.
- NFR-752: at least 9 of 10 deliberately unreadable photos end with no draft rows
  (REJECTED, or AI_UNAVAILABLE after the provider fails).
--provider claude needs AI_API_KEY and makes billed API calls; nothing else leaves the machine.
Results print as JSON; photos and truth values are never logged.
"""
import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.ingestion import InferenceService, apply_confidence_bands
from app.privacy import sanitize_image
from app.vision import EXTRACTION_PROMPT, ClaudeVisionModel, MockVisionModel, ProviderFatalError

HIGH_PRECISION_TARGET = 0.95
UNREADABLE_TARGET = 0.9


def _same(field, extracted, expected):
    if extracted is None or expected is None:
        return False
    if field == "itemName":
        return " ".join(str(extracted).split()).casefold() == " ".join(str(expected).split()).casefold()
    try:
        return Decimal(str(extracted)) == Decimal(str(expected))
    except InvalidOperation:
        return False


def score_page(draft, truth):
    """Return (high_total, high_correct, unreadable_ok or None) for one page."""
    if truth.get("unreadable"):
        return 0, 0, not draft["payload"]["lines"]
    high_total = high_correct = 0
    expected_lines = truth.get("lines", [])
    for index, line in enumerate(draft["payload"]["lines"]):
        expected = expected_lines[index] if index < len(expected_lines) else {}
        for field in ("itemName", "quantity", "unitPrice"):
            if line["bands"].get(field) == "HIGH":
                high_total += 1
                high_correct += _same(field, line.get(field), expected.get(field))
    return high_total, high_correct, None


def run(folder: Path, model) -> dict:
    truth = json.loads((folder / "truth.json").read_text(encoding="utf-8"))
    service = InferenceService(model, sleep=lambda _: None)
    high_total = high_correct = unreadable = unreadable_ok = 0
    for name, expected in sorted(truth.items()):
        image = sanitize_image((folder / name).read_bytes())
        result = service._call_provider(name, lambda image=image: model.extract_from_image(
            image, EXTRACTION_PROMPT, service.timeout_seconds))
        draft = ({"status": "AI_UNAVAILABLE", "payload": {"lines": []}} if result is None
                 else apply_confidence_bands(result, "IMAGE"))
        total, correct, unreadable_result = score_page(draft, expected)
        high_total += total
        high_correct += correct
        if unreadable_result is not None:
            unreadable += 1
            unreadable_ok += unreadable_result
    precision = high_correct / high_total if high_total else None
    unreadable_rate = unreadable_ok / unreadable if unreadable else None
    return {
        "pages": len(truth), "provider": model.name,
        "NFR-751": {"highFields": high_total, "highCorrect": high_correct, "precision": precision,
                    "target": HIGH_PRECISION_TARGET,
                    "passed": precision is not None and precision >= HIGH_PRECISION_TARGET},
        "NFR-752": {"unreadablePages": unreadable, "noRows": unreadable_ok, "rate": unreadable_rate,
                    "target": UNREADABLE_TARGET, "passed": unreadable >= 10 and unreadable_rate >= UNREADABLE_TARGET},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--provider", choices=("claude", "mock"), default="claude")
    args = parser.parse_args(argv)
    if args.provider == "claude":
        if not os.getenv("AI_API_KEY"):
            sys.exit("AI_API_KEY is not set: a real-provider benchmark needs an authorized key.")
        model = ClaudeVisionModel(os.environ["AI_API_KEY"], os.getenv("AI_EXTRACTION_MODEL") or "claude-sonnet-5-5")
    else:
        model = MockVisionModel()
    try:
        report = run(args.folder, model)
    except ProviderFatalError as exc:
        sys.exit(f"Provider error: {exc}")
    print(json.dumps(report, indent=2))
    return 0 if report["NFR-751"]["passed"] and report["NFR-752"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

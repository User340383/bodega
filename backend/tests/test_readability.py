"""NFR-731 and NFR-734 readability, with the SRS's own verification method (SRS 7.3: "Check each
message for cause, action, word count, grade level, and no codes"; "Count words and compute grade
level of displayed guidance"). Messages are collected from the code, so a new message is checked too.
"""
import json
import re
from pathlib import Path

import pytest

from app import guidance, ingestion, privacy, readability, repository
from app.analysis import build_analysis
from app.guidance import GuidanceEngine, template_message
from app.main import GENERIC_MESSAGES
from conftest import AS_OF, logged_dates

APP = Path(__file__).resolve().parents[1] / "app"
ACTION_WORDS = re.compile(r"\b(try|type|retake|choose|check|log|refresh|sign|open|start|discard|capture|"
                          r"ask|use|enter|wait|pick|paste|press|reopen)\b", re.IGNORECASE)
CODES = re.compile(r"\b[A-Z]{2,}_[A-Z_]+\b|\b(HTTP|JSON|SQL|stack|traceback|exception)\b|\b[45]\d\d\b", re.IGNORECASE)
PROVIDERS = re.compile(r"\b(claude|anthropic|openai|gpt)\b", re.IGNORECASE)


def owner_messages():
    """Every fixed message an owner can see from the backend (validation, failure, timeout, no-history)."""
    found = {f"ingestion.{k}": v for k, v in ingestion.MESSAGES.items() if k != "MOCK_IMAGE"}
    found |= {f"guidance.{k}": v for k, v in guidance.FAILURE_REASONS.items()}
    found |= {f"generic.{k}": v for k, v in GENERIC_MESSAGES.items()}
    found |= {"repository.STALE": repository.STALE_MESSAGE, "repository.NOT_FOUND": repository.NOT_FOUND,
              "repository.DAMAGED_DRAFT": repository.DAMAGED_DRAFT}
    source = "".join((APP / name).read_text(encoding="utf-8") for name in ("main.py", "privacy.py", "repository.py", "ingestion.py"))
    patterns = (r'"msg": "([^"]+)"', r'HTTPException\(\d{3}, "([^"]+)"\)', r'return "([A-Z][^"]+\.)"',
                r'ApiError\(\d{3}, "[A-Z_]+", f?"([^"]+)"\)', r'extraction_validation_error\("\w+", "([^"]+)"\)',
                r'InvalidImageError\("([^"]+)"\)')
    for pattern in patterns:
        for text in re.findall(pattern, source):
            found.setdefault(f"code: {text[:40]}", re.sub(r"\{[^}]+\}", "6", text))  # Fill format fields.
    return found


MESSAGES = owner_messages()


def test_calibration_matches_published_flesch_kincaid_values():
    assert readability.grade("The cat sat on the mat.") == pytest.approx(-1.45, abs=0.1)
    assert readability.syllables("readability") == 5 and readability.syllables("page") == 1
    assert len(MESSAGES) >= 40  # The collector really finds the messages.


@pytest.mark.parametrize("name", sorted(MESSAGES))
def test_every_owner_message_meets_nfr_731(name):
    text = MESSAGES[name]
    assert len(readability.words(text)) <= 30, text                     # (c)
    assert readability.grade(text) <= 8.0, (round(readability.grade(text), 1), text)  # (d)
    assert not CODES.search(text) and not PROVIDERS.search(text), text  # (e)
    assert ACTION_WORDS.search(text) or name.startswith(("code: Sale date", "code: Text must", "code: Choose")), text  # (b)


def test_srs_fixed_phrases_are_kept():
    assert ingestion.MESSAGES["AI_UNAVAILABLE"].startswith("AI reading is temporarily unavailable")  # FR-211
    assert ingestion.MESSAGES["UNREADABLE_IMAGE"].startswith("Image could not be read")             # FR-210
    assert {"flat", "light", "page"} <= set(re.findall(r"[a-z]+", ingestion.MESSAGES["UNREADABLE_IMAGE"]))


def analysis_for(trends):
    dates = logged_dates(14 if trends != ["INSUFFICIENT_DATA"] else 9)
    shapes = {"UP": ("2", "4"), "DOWN": ("4", "2"), "STABLE": ("3", "3"), "INSUFFICIENT_DATA": ("3", "3")}
    records = []
    for index, trend in enumerate(trends):
        earlier, recent = shapes[trend]
        records += [{"saleDate": day, "itemName": f"Item {chr(65 + index)}",
                     "quantity": recent if position >= len(dates) - 7 else earlier}
                    for position, day in enumerate(dates)]
    return build_analysis(records, AS_OF, dates[0])


@pytest.mark.parametrize("trend", ["UP", "DOWN", "STABLE", "INSUFFICIENT_DATA"])
def test_m3_template_meets_nfr_734_per_item(trend):
    estimate = analysis_for([trend]).estimates[0]
    text = template_message(estimate)
    assert len(readability.words(text)) <= 40 and readability.grade(text) <= 8.0, (readability.grade(text), text)


class Wordy:
    name = "wordy"

    def __init__(self, filler):
        self.filler = filler

    def generate(self, request):
        return json.dumps({"items": [{"itemName": e.itemName, "suggestedQty": e.suggestedQty,
                                      "message": f"{e.itemName}: consider having {e.suggestedQty} on hand. {self.filler}"}
                                     for e in request.items]})


def test_ai_guidance_over_150_words_is_withheld():
    source = analysis_for(["UP"] * 6)
    filler = "Sales are rising compared with the previous logged days. " * 3
    result = GuidanceEngine(Wordy(filler)).generate(source)
    assert (result.source, result.failedRule, result.groundingPassed) == ("TEMPLATE", "MESSAGE_TOO_LONG", False)


def test_ai_guidance_above_grade_8_is_withheld():
    source = analysis_for(["UP"])
    hard = "Consider restocking recent previous planning quantity averages averaging restocking."
    result = GuidanceEngine(Wordy(hard)).generate(source)
    assert (result.source, result.failedRule) == ("TEMPLATE", "HARD_TO_READ")


def test_short_plain_ai_guidance_is_shown():
    result = GuidanceEngine(Wordy("Check your stock.")).generate(analysis_for(["UP", "DOWN", "STABLE"]))
    assert result.source == "AI_WORDED" and result.groundingPassed
    text = " ".join(item.message for item in result.items)
    assert len(readability.words(text)) <= 150 and readability.grade(text) <= 8.0


def test_privacy_messages_are_collected():
    assert any("JPEG or PNG" in text for text in MESSAGES.values())
    assert privacy.text_problem("<b>") in MESSAGES.values()

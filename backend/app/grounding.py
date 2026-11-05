"""Validates provider wording against authoritative evidence.

The strongest rule is a closed vocabulary: apart from the item's own name, a message may use
only the planning words below. Claims about weather, prices, competitors or guarantees fail
because those words are not available, not because a blocklist anticipated them.

This reduces risk; it does not prove arbitrary natural language is true. Allowed words can
still be arranged misleadingly. Residual limitations are in docs/DIUSHEN_IMPLEMENTATION.md.
"""
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from .analysis import normalize_item
from .llm import ItemEvidence

MAX_MESSAGE_CHARS = 240  # Excludes the item's own name, whose length is unrestricted.
WINDOW_DAYS_TEXT = "7"

UP_WORDS = frozenset({"up", "rising", "rose", "increasing", "increased", "increase", "higher", "climbing"})
DOWN_WORDS = frozenset({"down", "falling", "fell", "decreasing", "decreased", "decrease", "lower", "declining"})
STABLE_WORDS = frozenset({"stable", "steady", "flat", "unchanged"})
TREND_WORDS = {"UP": UP_WORDS, "DOWN": DOWN_WORDS, "STABLE": STABLE_WORDS, "INSUFFICIENT_DATA": frozenset()}

# Excludes negation ("not", "no"), certainty ("will"), and stock states ("low", "out", "enough").
NEUTRAL_WORDS = frozenset("""
a about across all an and are as at average averaged averages averaging based been before by
check compared confirmed consider cover covers current date dates day days each earlier for
from guidance has history in is it its latest logged may might of on or order ordering over
per period plan planning previous prior qty quantity recent restock restocking sale sales
selling sold stock suggest suggested suggests than that the this to trend trending unit units
was were while with your actual
""".split())
ALLOWED_WORDS = NEUTRAL_WORDS | UP_WORDS | DOWN_WORDS | STABLE_WORDS

_MARKUP = re.compile(r"[<>&*_`\[\]#|\\]")
_ALLOWED_CHARS = re.compile(r"[A-Za-z0-9 .,:;'%()\-]*")
_NUMBER = re.compile(r"\d[\d,.]*\d|\d")
_VALID_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_FOLLOWING_WORDS = re.compile(r"\s*([A-Za-z]+)?(?:\s+([A-Za-z]+))?")
_WORD = re.compile(r"[A-Za-z]+")
_STOCK = re.compile(r"\bstock\b", re.IGNORECASE)
_STOCK_CHECK = re.compile(r"\bcheck\s+(?:your\s+)?(?:actual\s+|current\s+)?stock\b", re.IGNORECASE)
_WINDOW_FOLLOWERS = {"logged", "latest", "recent", "previous", "prior", "sales"}


@dataclass(frozen=True)
class GroundingResult:
    passed: bool
    failed_rule: str | None = None
    messages: dict[str, str] | None = None  # Normalized item key -> validated message.


def _fail(rule: str) -> GroundingResult:
    return GroundingResult(False, rule)


def _exact(text: str) -> Fraction:
    # Via Decimal: Fraction(str) goes through int(), which rejects very long digit strings.
    return Fraction(Decimal(text))


def _reject_constant(_):
    raise ValueError("Non-finite JSON number")


def _parse(raw) -> tuple[list | None, str | None]:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, "EMPTY_RESPONSE"
    if not isinstance(raw, str):
        return None, "INVALID_SCHEMA"
    try:
        payload = json.loads(raw, parse_float=Decimal, parse_int=Decimal, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return None, "MALFORMED_JSON"
    if not isinstance(payload, dict) or set(payload) != {"items"} or not isinstance(payload["items"], list):
        return None, "INVALID_SCHEMA"
    for entry in payload["items"]:
        if (not isinstance(entry, dict) or set(entry) != {"itemName", "suggestedQty", "message"}
                or not isinstance(entry["itemName"], str) or not isinstance(entry["message"], str)
                or not isinstance(entry["suggestedQty"], (str, Decimal))):
            return None, "INVALID_SCHEMA"
    return payload["items"], None


def _same_quantity(claimed, authoritative: str) -> bool:
    try:
        value = Decimal(claimed)
    except InvalidOperation:
        return False
    return value.is_finite() and Fraction(value) == _exact(authoritative)


def _check_numbers(body: str, item: ItemEvidence) -> str | None:
    quantity = _exact(item.suggestedQty)
    averages = {_exact(item.currentAverage)}
    if item.previousAverage is not None:
        averages.add(_exact(item.previousAverage))
    window = _exact(WINDOW_DAYS_TEXT)
    percent = _exact(item.changePercent) if item.changePercent is not None else None
    stated_quantity = False

    for match in _NUMBER.finditer(body):
        token = match.group()
        if not _VALID_NUMBER.fullmatch(token):
            return "UNSUPPORTED_NUMBER"
        value = _exact(token.replace(",", ""))
        rest = body[match.end():]
        if rest.lstrip().startswith("%"):
            if value != percent:
                return "UNSUPPORTED_NUMBER"
            continue
        if value not in averages | {quantity, window}:
            return "UNSUPPORTED_NUMBER"

        # A value that is allowed somewhere must also be used in its own role.
        following = _FOLLOWING_WORDS.match(rest)
        first = (following.group(1) or "").lower()
        second = (following.group(2) or "").lower()
        if first == "per" or (first in ("unit", "units") and second == "per"):
            if value not in averages:
                return "UNSUPPORTED_NUMBER"
        elif first in ("unit", "units"):
            if value != quantity:
                return "UNSUPPORTED_NUMBER"
        elif value == window and value != quantity and value not in averages:
            if first not in _WINDOW_FOLLOWERS:
                return "UNSUPPORTED_NUMBER"
        if value == quantity:
            stated_quantity = True
    return None if stated_quantity else "QUANTITY_MISMATCH"


def _check_message(message: str, item: ItemEvidence) -> str | None:
    if not message.strip():
        return "INVALID_SCHEMA"
    name = re.compile(rf"(?<![A-Za-z0-9]){re.escape(item.itemName)}(?![A-Za-z0-9])", re.IGNORECASE)
    if not name.search(message):
        return "ITEM_NOT_NAMED"
    # The owner's label may contain digits or symbols ("Coke 2L"); those are not claims.
    body = name.sub(" ", message)
    if _MARKUP.search(body):
        return "UNSAFE_MARKUP"
    if len(body) > MAX_MESSAGE_CHARS:
        return "MESSAGE_TOO_LONG"
    number_rule = _check_numbers(body, item)
    if number_rule:
        return number_rule

    words = [word.lower() for word in _WORD.findall(body)]
    if any(word in (UP_WORDS | DOWN_WORDS | STABLE_WORDS) - TREND_WORDS[item.trend] for word in words):
        return "TREND_MISMATCH"
    if not _ALLOWED_CHARS.fullmatch(body) or any(word not in ALLOWED_WORDS for word in words):
        return "UNSUPPORTED_CLAIM"
    # "Stock" may appear only as advice to check it: inventory is never recorded.
    if len(_STOCK.findall(body)) != len(_STOCK_CHECK.findall(body)):
        return "UNSUPPORTED_CLAIM"
    return None


def check_grounding(raw, evidence: tuple[ItemEvidence, ...]) -> GroundingResult:
    entries, rule = _parse(raw)
    if rule:
        return _fail(rule)

    known = {normalize_item(item.itemName): item for item in evidence}
    by_key = {}
    for entry in entries:
        key = normalize_item(entry["itemName"])
        if key not in known:
            return _fail("UNKNOWN_ITEM")
        if key in by_key:
            return _fail("DUPLICATE_ITEM")
        by_key[key] = entry
    if set(by_key) != set(known):
        return _fail("MISSING_ITEM")

    for key, entry in by_key.items():
        if not _same_quantity(entry["suggestedQty"], known[key].suggestedQty):
            return _fail("QUANTITY_MISMATCH")
    for key, entry in by_key.items():
        rule = _check_message(entry["message"], known[key])
        if rule:
            return _fail(rule)
    return GroundingResult(True, messages={key: entry["message"].strip() for key, entry in by_key.items()})

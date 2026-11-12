"""Flesch-Kincaid grade level and word counts for NFR-731 and NFR-734.

grade = 0.39 * (words / sentences) + 11.8 * (syllables / words) - 15.59

Syllables are estimated per word by counting vowel groups, ignoring a silent final "e" and
counting a final consonant + "le" (the usual estimator in readability tools). Numbers count as
one word of one syllable. The estimate is deterministic, so a test can hold every message to the
SRS limits; it is an approximation of human syllable counts, as every automated grader is.
"""
import re

_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:[.,]\d+)*")
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")
_VOWEL_GROUPS = re.compile(r"[aeiouy]+")


def words(text: str) -> list[str]:
    return _WORD.findall(text)


def syllables(word: str) -> int:
    if word[0].isdigit():
        return 1
    w = word.lower().replace("'", "")
    count = len(_VOWEL_GROUPS.findall(w))
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")) and count > 1:
        count -= 1  # Silent final "e": "make", "page".
    if w.endswith("le") and len(w) > 2 and w[-3] not in "aeiouy" and count == 0:
        count = 1
    return max(count, 1)


def sentence_count(text: str) -> int:
    return max(len(_SENTENCE_END.findall(text.strip())), 1)


def grade(text: str) -> float:
    tokens = words(text)
    if not tokens:
        return 0.0
    total = sum(syllables(token) for token in tokens)
    return 0.39 * (len(tokens) / sentence_count(text)) + 11.8 * (total / len(tokens)) - 15.59

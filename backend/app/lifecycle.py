"""Milestone 3 state and confidence rules shared by persistence callers."""
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class SubmissionStatus(StrEnum):
    UPLOADED = 'UPLOADED'
    CLASSIFYING = 'CLASSIFYING'
    NEEDS_VERIFICATION = 'NEEDS_VERIFICATION'
    CONFIRMED = 'CONFIRMED'
    REJECTED = 'REJECTED'
    AI_UNAVAILABLE = 'AI_UNAVAILABLE'


TRANSITIONS = {
    'UPLOADED': {'CLASSIFYING', 'AI_UNAVAILABLE'},
    'CLASSIFYING': {'NEEDS_VERIFICATION', 'REJECTED', 'AI_UNAVAILABLE'},
    'NEEDS_VERIFICATION': {'CONFIRMED'},
    'AI_UNAVAILABLE': {'CONFIRMED'},
    'REJECTED': {'CONFIRMED'},
    'CONFIRMED': set(),
}


def confidence_score(value):
    try:
        if isinstance(value, bool):
            raise ValueError('Confidence must be a finite number from 0 to 1')
        score = Decimal(str(value))
        if not score.is_finite() or not 0 <= score <= 1:
            raise ValueError('Confidence must be a finite number from 0 to 1')
        return score
    except InvalidOperation as exc:
        raise ValueError('Confidence must be a finite number from 0 to 1') from exc


def confidence_band(value):
    if value is None:
        return 'UNSCORED'
    score = confidence_score(value)
    return 'HIGH' if score >= Decimal('0.85') else 'MEDIUM' if score >= Decimal('0.60') else 'LOW'


def extraction_status(overall_confidence, lines):
    return (SubmissionStatus.REJECTED if confidence_score(overall_confidence) < Decimal('0.40') or not lines
            else SubmissionStatus.NEEDS_VERIFICATION)

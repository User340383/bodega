"""BaselineAnalyzer: seven-logged-date moving averages and trend classification.

Exactness: quantities are NUMERIC in PostgreSQL and arrive as Decimal. Decimal arithmetic
rounds to the active context (28 significant digits by default) and an average over seven
dates usually has no finite decimal form, so every calculation uses fractions.Fraction.
pandas holds them in object-dtype columns, where groupby/unstack/cumsum/shift call Python's
exact operators. DataFrame.rolling() is deliberately not used because it converts to float.
Numbers leave this module only as strings. See docs/DIUSHEN_IMPLEMENTATION.md.
"""
import math
import re
from collections.abc import Callable, Iterable
from itertools import pairwise
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Literal

import pandas as pd
from fastapi import HTTPException
from pydantic import BaseModel, BeforeValidator

from . import repository
from .clock import Clock

WINDOW_DAYS = 7
TREND_REQUIRED_DAYS = 2 * WINDOW_DAYS
HISTORY_CALENDAR_DAYS = 28
ZERO = Fraction(0)

Trend = Literal["UP", "DOWN", "STABLE", "INSUFFICIENT_DATA"]
AnalysisStatus = Literal["NO_DATA", "INSUFFICIENT_HISTORY", "ESTIMATE_ONLY", "READY"]

QUANTITY_BASIS = (
    "Suggested quantities are sales-based planning guidance: the rounded-up total of the latest "
    "7 logged sales dates. They do not reflect actual stock and are not a calendar-week forecast."
)


def _iso_date_only(value):
    """Reject forms pydantic would otherwise coerce into a date, such as timestamps."""
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    raise ValueError("Date must use the YYYY-MM-DD format")


IsoDate = Annotated[date, BeforeValidator(_iso_date_only)]


class AnalysisWindow(BaseModel):
    start: date
    end: date
    loggedDays: int


# Quantities and averages are exact Decimals and leave the API as JSON numbers (M3 4.2);
# the strings from decimal_text are parsed exactly by pydantic.
class SeriesPoint(BaseModel):
    date: date
    quantity: Decimal
    observed: bool  # False: the business logged this date without the item, so it counts as 0.
    movingAverage: Decimal | None
    movingAverageRounded: bool
    calendarDaysSkipped: int  # Unlogged calendar days since the previous logged date.


class ItemEstimate(BaseModel):
    itemKey: str
    itemName: str
    windowLoggedDays: int
    currentAverage: Decimal | None
    currentAverageRounded: bool
    # M3 4.3/9.6: the average shown to the owner, rounded half-up to 1 decimal.
    currentAverageDisplay: str | None
    currentTotal: Decimal | None
    currentDaysWithSales: int | None
    previousAverage: Decimal | None
    previousAverageRounded: bool
    previousTotal: Decimal | None
    previousDaysWithSales: int | None
    trend: Trend
    changePercent: Decimal | None
    suggestedQty: Decimal | None
    series: list[SeriesPoint]


class AnalysisResponse(BaseModel):
    asOf: date
    historyStart: date
    historyEnd: date
    loggedDays: int
    requiredDays: int = WINDOW_DAYS
    trendRequiredDays: int = TREND_REQUIRED_DAYS
    status: AnalysisStatus
    statusMessage: str
    quantityBasis: str = QUANTITY_BASIS
    loggedDates: list[date]
    currentWindow: AnalysisWindow | None
    previousWindow: AnalysisWindow | None
    estimates: list[ItemEstimate]


def normalize_item(label: str) -> str:
    """Matching key: surrounding whitespace and case are ignored; synonyms are not merged."""
    return label.strip().casefold()


def classify_trend(current: Fraction, previous: Fraction) -> Trend:
    """Exact thresholds: +10% and -10% themselves are STABLE."""
    if previous == 0:
        return "UP" if current > 0 else "STABLE"
    if current * 10 > previous * 11:
        return "UP"
    if current * 10 < previous * 9:
        return "DOWN"
    return "STABLE"


def _integer_text(value: int) -> str:
    # str(int) raises above Python's 4,300-digit limit; NUMERIC values can be longer.
    return format(Decimal(value), "f")


def _scaled_text(scaled: int, scale: int) -> str:
    digits = _integer_text(abs(scaled)).rjust(scale + 1, "0")
    text = digits[:-scale] + "." + digits[-scale:] if scale else digits
    text = text.rstrip("0").rstrip(".") if scale else text
    return ("-" if scaled < 0 else "") + text


def decimal_text(value: Fraction, places: int = 6) -> tuple[str, bool]:
    """Return (text, rounded). Terminating values are exact; others keep `places` decimal
    places and at least `places` significant digits, rounded half-even."""
    remaining, twos, fives = value.denominator, 0, 0
    while remaining % 2 == 0:
        remaining, twos = remaining // 2, twos + 1
    while remaining % 5 == 0:
        remaining, fives = remaining // 5, fives + 1
    if remaining == 1:
        scale = max(twos, fives)
        return _scaled_text(value.numerator * 10**scale // value.denominator, scale), False
    magnitude = Decimal(value.denominator).adjusted() - Decimal(abs(value.numerator) or 1).adjusted()
    scale = max(places, magnitude + places)
    return _scaled_text(round(value * 10**scale), scale), True


def one_decimal(value: Fraction) -> str:
    """Round half-up to one decimal place, exactly (M3 9.6 "average rounded to 1 decimal")."""
    tenths = math.floor(value * 10 + Fraction(1, 2))
    return f"{_integer_text(tenths // 10)}.{tenths % 10}"


def _empty_response(as_of: date, start: date) -> AnalysisResponse:
    return AnalysisResponse(
        asOf=as_of, historyStart=start, historyEnd=as_of, loggedDays=0, status="NO_DATA",
        statusMessage="No confirmed sales exist in this 28-day period.",
        loggedDates=[], currentWindow=None, previousWindow=None, estimates=[],
    )


def _status_for(logged_days: int) -> tuple[AnalysisStatus, str]:
    if logged_days < WINDOW_DAYS:
        return "INSUFFICIENT_HISTORY", (
            f"{logged_days} of {WINDOW_DAYS} logged sales dates. "
            f"{WINDOW_DAYS - logged_days} more needed for an estimate."
        )
    if logged_days < TREND_REQUIRED_DAYS:
        return "ESTIMATE_ONLY", (
            f"{logged_days} of {TREND_REQUIRED_DAYS} logged sales dates. "
            f"Estimates are available; {TREND_REQUIRED_DAYS - logged_days} more needed for a trend."
        )
    return "READY", f"Estimates and trends use the latest {TREND_REQUIRED_DAYS} of {logged_days} logged sales dates."


def build_analysis(records: Iterable[dict], as_of: date, start: date) -> AnalysisResponse:
    """Pure calculation over {"saleDate", "itemName", "quantity"} rows inside the bounds."""
    rows = list(records)
    if not rows:
        return _empty_response(as_of, start)
    frame = pd.DataFrame({
        "saleDate": pd.Series([row["saleDate"] for row in rows], dtype=object),
        "itemKey": pd.Series([normalize_item(row["itemName"]) for row in rows], dtype=object),
        "itemName": pd.Series([row["itemName"].strip() for row in rows], dtype=object),
        "quantity": pd.Series([Fraction(row["quantity"]) for row in rows], dtype=object),
    }).sort_values("saleDate", kind="stable")

    # Display label: the most recent spelling. Grouping removes same-date duplicates.
    labels = frame.groupby("itemKey")["itemName"].last()
    daily = frame.groupby(["saleDate", "itemKey"])["quantity"].sum()
    observed = set(daily.index)

    # One row per logged date, one column per item. fill_value makes an item's absence on a
    # logged date count as zero; dates nobody logged never become rows, so they stay unknown.
    matrix = daily.unstack("itemKey", fill_value=ZERO).sort_index()
    logged_dates = list(matrix.index)
    logged_days = len(logged_dates)

    # Exact rolling sum: running total minus the running total seven logged dates earlier.
    running = matrix.cumsum()
    window_totals = running - running.shift(WINDOW_DAYS, fill_value=ZERO)
    moving_averages = window_totals / WINDOW_DAYS

    status, message = _status_for(logged_days)
    has_estimate = logged_days >= WINDOW_DAYS
    has_trend = logged_days >= TREND_REQUIRED_DAYS
    current_dates = logged_dates[-WINDOW_DAYS:] if has_estimate else []
    previous_dates = logged_dates[-TREND_REQUIRED_DAYS:-WINDOW_DAYS] if has_trend else []
    skipped = [0] + [(later - earlier).days - 1 for earlier, later in pairwise(logged_dates)]

    estimates = []
    for item_key in matrix.columns:
        series = []
        for position, sale_date in enumerate(logged_dates):
            average = moving_averages[item_key].iloc[position] if position >= WINDOW_DAYS - 1 else None
            average_text, average_rounded = decimal_text(average) if average is not None else (None, False)
            series.append(SeriesPoint(
                date=sale_date,
                quantity=decimal_text(matrix[item_key].iloc[position])[0],
                observed=(sale_date, item_key) in observed,
                movingAverage=average_text, movingAverageRounded=average_rounded,
                calendarDaysSkipped=skipped[position],
            ))

        current = previous = current_total = previous_total = None
        if has_estimate:
            current_total = window_totals[item_key].iloc[-1]
            current = moving_averages[item_key].iloc[-1]
        if has_trend:
            previous_total = window_totals[item_key].iloc[-1 - WINDOW_DAYS]
            previous = moving_averages[item_key].iloc[-1 - WINDOW_DAYS]

        current_text, current_rounded = decimal_text(current) if current is not None else (None, False)
        previous_text, previous_rounded = decimal_text(previous) if previous is not None else (None, False)
        change = None
        if has_trend and previous > 0:
            change = decimal_text((current - previous) / previous * 100, places=2)[0]
        estimates.append(ItemEstimate(
            itemKey=item_key, itemName=labels[item_key], windowLoggedDays=WINDOW_DAYS,
            currentAverage=current_text, currentAverageRounded=current_rounded,
            currentAverageDisplay=one_decimal(current) if current is not None else None,
            currentTotal=decimal_text(current_total)[0] if has_estimate else None,
            currentDaysWithSales=sum((day, item_key) in observed for day in current_dates) if has_estimate else None,
            previousAverage=previous_text, previousAverageRounded=previous_rounded,
            previousTotal=decimal_text(previous_total)[0] if has_trend else None,
            previousDaysWithSales=sum((day, item_key) in observed for day in previous_dates) if has_trend else None,
            trend=classify_trend(current, previous) if has_trend else "INSUFFICIENT_DATA",
            changePercent=change,
            suggestedQty=_integer_text(math.ceil(current * WINDOW_DAYS)) if has_estimate else None,
            series=series,
        ))

    def window(dates):
        return AnalysisWindow(start=dates[0], end=dates[-1], loggedDays=len(dates)) if dates else None

    return AnalysisResponse(
        asOf=as_of, historyStart=start, historyEnd=as_of, loggedDays=logged_days,
        status=status, statusMessage=message, loggedDates=logged_dates,
        currentWindow=window(current_dates), previousWindow=window(previous_dates),
        estimates=estimates,
    )


def snapshots(result: AnalysisResponse) -> list[dict]:
    """BaselineEstimate rows for SD-03 saveBaselines: one per item that has an estimate."""
    rows = []
    for estimate in result.estimates:
        if estimate.suggestedQty is None:
            continue
        rows.append({
            "itemName": estimate.itemName, "itemKey": estimate.itemKey, "windowDays": WINDOW_DAYS,
            "loggedDays": result.loggedDays, "averageDailyQuantity": Decimal(estimate.currentAverage),
            "priorAverageDailyQuantity": Decimal(estimate.previousAverage) if estimate.previousAverage else None,
            "trend": estimate.trend, "suggestedRestockQty": int(estimate.suggestedQty),
        })
    return rows


class BaselineAnalyzer:
    """Computes results fresh from confirmed records every time (FR-404).

    With save_snapshots, the results are also stored as BASELINE_ESTIMATES snapshots for that
    asOf date, replacing earlier ones (SD-03 saveBaselines, M3 7.1). Snapshots are an audit
    record only: they are never read back to produce a response, so they cannot go stale.
    """

    def __init__(self, fetch_records: Callable[[date, date], list[dict]] | None = None,
                 save_snapshots: Callable[[date, list[dict]], None] | None = None):
        self._fetch_records = fetch_records or repository.get_confirmed_sales_between
        self._save_snapshots = save_snapshots or repository.replace_baselines

    def analyze(self, as_of: date, *, save_snapshots: bool = False) -> AnalysisResponse:
        start = as_of - timedelta(days=HISTORY_CALENDAR_DAYS - 1)
        result = build_analysis(self._fetch_records(start, as_of), as_of, start)
        if save_snapshots:
            self._save_snapshots(as_of, snapshots(result))
        return result


def resolve_as_of(requested: date | None, clock: Clock, location: list[str]) -> date:
    """Shared by analysis and guidance so both apply one default and one validation policy."""
    today = clock.today()
    as_of = requested or today
    problem = None
    if as_of > today:
        problem = f"asOf cannot be after the current business date ({today.isoformat()})"
    elif as_of < date.min + timedelta(days=HISTORY_CALENDAR_DAYS - 1):
        problem = "asOf is too early to calculate a 28-day history period"
    if problem:
        # M3 4.2: GET /analysis answers 400 for an invalid asOf.
        raise HTTPException(400, detail=[{"loc": location, "msg": problem, "type": "value_error"}])
    return as_of

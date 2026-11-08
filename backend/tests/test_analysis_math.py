"""Exact analysis math without a database: BaselineAnalyzer with injected records."""
from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from app.analysis import BaselineAnalyzer, build_analysis, classify_trend, decimal_text
from conftest import AS_OF, logged_dates

START = date(2026, 4, 3)


def rows(dates, quantity, item='Bread'):
    return [{'saleDate': day, 'itemName': item, 'quantity': Decimal(str(quantity))} for day in dates]


def only(result):
    assert len(result.estimates) == 1
    return result.estimates[0]


def test_required_worked_example():
    dates = logged_dates(14)
    records = rows(dates[:7], 2) + rows(dates[7:], 4)
    estimate = only(build_analysis(records, AS_OF, START))
    assert estimate.previousAverage == '2'
    assert estimate.currentAverage == '4'
    assert estimate.trend == 'UP'
    assert estimate.suggestedQty == '28'
    assert estimate.changePercent == '100'


def test_analyzer_requests_the_inclusive_28_day_bounds():
    requested = []
    result = BaselineAnalyzer(lambda start, end: requested.append((start, end)) or []).analyze(AS_OF)
    assert requested == [(START, AS_OF)]
    assert (result.historyStart, result.historyEnd) == (START, AS_OF)


@pytest.mark.parametrize('current,previous,expected', [
    ('11', '10', 'STABLE'),  # Exactly +10%.
    ('9', '10', 'STABLE'),  # Exactly -10%.
    ('10', '10', 'STABLE'),
    ('11.000000000000000000000000000001', '10', 'UP'),
    ('8.999999999999999999999999999999', '10', 'DOWN'),
    ('1.1', '1', 'STABLE'),
    ('0.9', '1', 'STABLE'),
    ('1', '0', 'UP'),
    ('0', '0', 'STABLE'),
    ('0', '1', 'DOWN'),
])
def test_trend_thresholds_are_exact(current, previous, expected):
    assert classify_trend(Fraction(Decimal(current)), Fraction(Decimal(previous))) == expected


def test_zero_prior_average_reports_up_without_a_percentage():
    dates = logged_dates(14)
    records = rows(dates[:7], 1, 'Milk') + rows(dates[7:], 3, 'Bread')
    bread = {e.itemKey: e for e in build_analysis(records, AS_OF, START).estimates}['bread']
    assert (bread.previousAverage, bread.trend, bread.changePercent) == ('0', 'UP', None)


@pytest.mark.parametrize('total,expected', [('10', '10'), ('10.0000001', '11'), ('0.1', '1'), ('21', '21')])
def test_planning_quantity_is_the_exact_ceiling(total, expected):
    # 10/7*7 is 10.000000000000002 in floats, which would wrongly round up to 11.
    dates = logged_dates(7)
    records = rows(dates[:1], total)
    records += [{'saleDate': day, 'itemName': 'Other', 'quantity': Decimal('1')} for day in dates]
    bread = {e.itemKey: e for e in build_analysis(records, AS_OF, START).estimates}['bread']
    assert bread.suggestedQty == expected


def test_repeating_average_is_flagged_rounded_while_total_stays_exact():
    dates = logged_dates(7)
    estimate = only(build_analysis(rows(dates[:1], 10) + rows(dates[1:], 1), AS_OF, START))
    assert estimate.currentTotal == '16'
    assert estimate.currentAverage == '2.285714'
    assert estimate.currentAverageRounded is True
    assert estimate.suggestedQty == '16'


def test_precision_survives_values_float_and_default_decimal_cannot_hold():
    dates = logged_dates(7)
    large = Decimal('123456789012345678901.1234567')
    records = [{'saleDate': day, 'itemName': 'Bread', 'quantity': large} for day in dates]
    records += [{'saleDate': dates[0], 'itemName': 'Bread', 'quantity': Decimal('0.0000000000000000000000000000001')}]
    estimate = only(build_analysis(records, AS_OF, START))
    # Default Decimal context (28 digits) and float would both lose these trailing digits.
    assert Fraction(Decimal(estimate.currentTotal)) == 7 * Fraction(large) + Fraction(1, 10**31)


def test_float_artifacts_do_not_appear():
    dates = logged_dates(7)
    records = rows(dates[:1], '0.1') + rows(dates[:1], '0.2') + rows(dates[1:], 1)
    estimate = only(build_analysis(records, AS_OF, START))
    assert estimate.series[0].quantity == '0.3'
    assert estimate.currentTotal == '6.3'
    assert estimate.currentAverage == '0.9'
    assert estimate.currentAverageRounded is False


@pytest.mark.parametrize('quantity,expected_qty', [
    ('1e-400', 1),  # A tiny positive total still rounds up to one.
    ('1' + '0' * 5000, 7 * 10**5000),  # Longer than Python's 4,300-digit int-to-str limit.
], ids=['tiny', 'huge'])
def test_extreme_numeric_values_do_not_fail(quantity, expected_qty):
    estimate = only(build_analysis(rows(logged_dates(7), quantity), AS_OF, START))
    assert Fraction(Decimal(estimate.currentAverage)) == Fraction(Decimal(quantity))
    assert Fraction(Decimal(estimate.suggestedQty)) == expected_qty


def test_decimal_text_keeps_significant_digits_for_tiny_repeating_values():
    text, rounded = decimal_text(Fraction(1, 7) * Fraction(1, 10**20))
    assert rounded and text.startswith('0.' + '0' * 20 + '142857')
    assert decimal_text(Fraction(-5, 2)) == ('-2.5', False)
    assert decimal_text(Fraction(0)) == ('0', False)
    assert decimal_text(Fraction(100)) == ('100', False)


def test_labels_normalize_case_and_whitespace_without_merging_synonyms():
    dates = logged_dates(7)
    records = [{'saleDate': day, 'itemName': name, 'quantity': Decimal('1')}
               for day, name in zip(dates, ['bread', ' BREAD ', 'Bread', 'bread\t', 'BREAD', 'bread', 'Bread '], strict=True)]
    records.append({'saleDate': dates[0], 'itemName': 'Loaf', 'quantity': Decimal('1')})
    estimates = {e.itemKey: e for e in build_analysis(records, AS_OF, START).estimates}
    assert set(estimates) == {'bread', 'loaf'}
    assert estimates['bread'].itemName == 'Bread'  # Most recent spelling, trimmed.
    assert estimates['bread'].currentTotal == '7'

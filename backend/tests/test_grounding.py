"""Grounding rules and GuidanceEngine behaviour using MockLLMService, without a database."""
import inspect
import json
import logging
import time
from dataclasses import asdict
from datetime import date
from decimal import Decimal

import pytest

from app.analysis import build_analysis
from app.grounding import MAX_MESSAGE_CHARS, check_grounding
from app.guidance import GuidanceEngine, STOCK_NOTICE, template_message
from app.llm import ItemEvidence, MockLLMService, MockScenario, UnavailableWordingProvider
from conftest import AS_OF, logged_dates

START = date(2026, 4, 3)
BREAD = ItemEvidence('Bread', '28', '4', '2', 'UP', '100', 7)


def provider_reply(message, item='Bread', qty='28'):
    return json.dumps({'items': [{'itemName': item, 'suggestedQty': qty, 'message': message}]})


def analysis(days=14):
    dates = logged_dates(days)
    records = []
    for position, day in enumerate(dates):
        recent = position >= days - 7
        records.append({'saleDate': day, 'itemName': 'Bread', 'quantity': Decimal('4' if recent else '2')})
        records.append({'saleDate': day, 'itemName': 'Milk', 'quantity': Decimal('2.5')})
    return build_analysis(records, AS_OF, START)


@pytest.mark.parametrize('message', [
    'Bread: plan 28 units to cover 7 logged sales dates.',
    'Bread: plan 28 units. Average is 4 per logged date, up from 2. Sales are up 100%.',
    'Plan 28 units of bread. Check your actual stock before ordering.',
])
def test_supported_wording_passes(message):
    result = check_grounding(provider_reply(message), (BREAD,))
    assert result.passed and result.failed_rule is None
    assert result.messages == {'bread': message}


@pytest.mark.parametrize('raw,rule', [
    ('', 'EMPTY_RESPONSE'),
    ('   ', 'EMPTY_RESPONSE'),
    (None, 'EMPTY_RESPONSE'),
    (42, 'INVALID_SCHEMA'),
    ('{"items": [', 'MALFORMED_JSON'),
    ('```json\n{"items": []}\n```', 'MALFORMED_JSON'),
    ('{"items": NaN}', 'MALFORMED_JSON'),
    ('[]', 'INVALID_SCHEMA'),
    ('{"entries": []}', 'INVALID_SCHEMA'),
    ('{"items": {}}', 'INVALID_SCHEMA'),
    ('{"items": ["Bread"]}', 'INVALID_SCHEMA'),
    ('{"items": [{"itemName": "Bread", "suggestedQty": "28"}]}', 'INVALID_SCHEMA'),
    ('{"items": [{"itemName": "Bread", "suggestedQty": true, "message": "x"}]}', 'INVALID_SCHEMA'),
    ('{"items": [{"itemName": "Bread", "suggestedQty": "28", "message": 5}]}', 'INVALID_SCHEMA'),
    ('{"items": [{"itemName": "Bread", "suggestedQty": "28", "message": "x", "price": 1}]}', 'INVALID_SCHEMA'),
    ('{"items": []}', 'MISSING_ITEM'),
])
def test_unusable_responses(raw, rule):
    result = check_grounding(raw, (BREAD,))
    assert not result.passed and result.failed_rule == rule and result.messages is None


@pytest.mark.parametrize('message,rule', [
    # Fabricated quantities and numbers, including an allowed number used in the wrong role.
    ('Bread: plan 29 units.', 'UNSUPPORTED_NUMBER'),
    ('Bread: plan 28 units, up from 99.', 'UNSUPPORTED_NUMBER'),
    ('Bread: plan 4 units. Quantity is 28.', 'UNSUPPORTED_NUMBER'),
    ('Bread: plan 28 units. Average is 28 per logged date.', 'UNSUPPORTED_NUMBER'),
    ('Bread: plan 28 units for 7 weeks.', 'UNSUPPORTED_NUMBER'),
    ('Bread: plan 28 units. Sales are up 50%.', 'UNSUPPORTED_NUMBER'),
    ('Bread: plan 1.2.3 units and 28 units.', 'UNSUPPORTED_NUMBER'),
    ('Bread sales are up.', 'QUANTITY_MISMATCH'),
    # Trend wording.
    ('Bread: plan 28 units. Sales are falling.', 'TREND_MISMATCH'),
    ('Bread: plan 28 units. Sales are stable.', 'TREND_MISMATCH'),
    # Unsupported claims fail because the words are unavailable.
    ('Bread: plan 28 units because rain is expected.', 'UNSUPPORTED_CLAIM'),
    ('Bread: plan 28 units to beat your competitor.', 'UNSUPPORTED_CLAIM'),
    ('Bread: plan 28 units for higher profit.', 'UNSUPPORTED_CLAIM'),
    ('Bread: plan 28 units. This is guaranteed to sell.', 'UNSUPPORTED_CLAIM'),
    ('Bread: plan 28 units, sales will not drop.', 'UNSUPPORTED_CLAIM'),
    ('Bread: plan twenty eight units.', 'QUANTITY_MISMATCH'),
    ('Bread: plan 28 units. Your price is 28 dollars.', 'UNSUPPORTED_CLAIM'),
    ('Bread: plan 28 units at $28.', 'UNSUPPORTED_CLAIM'),
    ('Bread: plan 28 units. Stock is up.', 'UNSUPPORTED_CLAIM'),
    ('Bread: plan 28 units. Your stock is low.', 'UNSUPPORTED_CLAIM'),
    ('Bread: plan 28 units.\nBuy now', 'UNSUPPORTED_CLAIM'),
    ('Bread: planifique 28 units.', 'UNSUPPORTED_CLAIM'),
    # Structure of the message.
    ('Plan 28 units.', 'ITEM_NOT_NAMED'),
    ('Bread: plan 28 units. <img src=x onerror=alert(1)>', 'UNSAFE_MARKUP'),
    ('Bread: plan **28** units.', 'UNSAFE_MARKUP'),
    ('Bread: plan 28 units. ' + 'sales ' * 60, 'MESSAGE_TOO_LONG'),
])
def test_rejected_messages(message, rule):
    assert check_grounding(provider_reply(message), (BREAD,)).failed_rule == rule


def test_fabricated_price_with_digits_is_rejected():
    assert check_grounding(provider_reply('Bread: plan 28 units at 3.50 each.'), (BREAD,)).failed_rule == 'UNSUPPORTED_NUMBER'


def test_item_identity_rules():
    milk = ItemEvidence('Milk', '18', '2.5', '2.5', 'STABLE', '0', 7)
    bread_entry = {'itemName': 'Bread', 'suggestedQty': '28', 'message': 'Bread: plan 28 units.'}
    milk_entry = {'itemName': 'Milk', 'suggestedQty': '18', 'message': 'Milk: plan 18 units.'}
    fake = {'itemName': 'Caviar', 'suggestedQty': '1', 'message': 'Caviar: plan 1 units.'}
    def run(*entries):
        return check_grounding(json.dumps({'items': list(entries)}), (BREAD, milk))
    assert run(bread_entry, milk_entry).passed
    assert run(bread_entry, milk_entry, fake).failed_rule == 'UNKNOWN_ITEM'
    assert run(bread_entry).failed_rule == 'MISSING_ITEM'
    assert run(bread_entry, bread_entry, milk_entry).failed_rule == 'DUPLICATE_ITEM'
    # Another item's name is outside this entry's vocabulary.
    crossing = {**bread_entry, 'message': 'Bread: plan 28 units over milk.'}
    assert run(crossing, milk_entry).failed_rule == 'UNSUPPORTED_CLAIM'


def test_quantity_field_must_match_exactly_and_labels_are_not_claims():
    assert check_grounding(provider_reply('Bread: plan 28 units.', qty='28.5'), (BREAD,)).failed_rule == 'QUANTITY_MISMATCH'
    assert check_grounding(provider_reply('Bread: plan 28 units.', qty='abc'), (BREAD,)).failed_rule == 'QUANTITY_MISMATCH'
    assert check_grounding(provider_reply('Bread: plan 28 units.', qty='28.0'), (BREAD,)).passed
    raw = json.dumps({'items': [{'itemName': 'Bread', 'suggestedQty': 28, 'message': 'Bread: plan 28 units.'}]})
    assert check_grounding(raw, (BREAD,)).passed
    label = ItemEvidence('Coke 2L & Co <Promo>', '28', '4', '2', 'UP', '100', 7)
    assert check_grounding(provider_reply('Coke 2L & Co <Promo>: plan 28 units.', item=label.itemName), (label,)).passed


def test_trend_words_are_unavailable_without_a_trend():
    item = ItemEvidence('Bread', '28', '4', None, 'INSUFFICIENT_DATA', None, 7)
    assert check_grounding(provider_reply('Bread: plan 28 units. Sales are stable.'), (item,)).failed_rule == 'TREND_MISMATCH'
    assert check_grounding(provider_reply('Bread: plan 28 units. Average is 4 per logged date.'), (item,)).passed


def test_message_length_excludes_the_unrestricted_item_name():
    label = 'Item ' + 'x' * 500
    item = ItemEvidence(label, '28', '4', '2', 'UP', '100', 7)
    assert check_grounding(provider_reply(f'{label}: plan 28 units.', item=label), (item,)).passed
    assert MAX_MESSAGE_CHARS == 240


def test_default_timeout_is_15_seconds():
    assert inspect.signature(GuidanceEngine).parameters['timeout_seconds'].default == 15.0


def test_valid_mock_wording_is_used_with_authoritative_fields():
    provider = MockLLMService()
    result = GuidanceEngine(provider).generate(analysis())
    assert (result.source, result.groundingPassed, result.failedRule, result.provider) == ('AI_WORDED', True, None, 'mock')
    bread = result.items[0]
    assert (bread.itemName, bread.suggestedQty, bread.trend) == ('Bread', '28', 'UP')
    assert bread.message == ('Bread: plan 28 units to cover 7 logged sales dates. '
                             'Average is 4 per logged date. Sales trend is up.')
    assert result.stockNotice == STOCK_NOTICE
    assert len(provider.calls) == 1


@pytest.mark.parametrize('scenario,rule', [
    (MockScenario.UNKNOWN_ITEM, 'UNKNOWN_ITEM'),
    (MockScenario.CHANGED_QUANTITY, 'QUANTITY_MISMATCH'),
    (MockScenario.UNSUPPORTED_NUMBER, 'UNSUPPORTED_NUMBER'),
    (MockScenario.WRONG_TREND, 'TREND_MISMATCH'),
    (MockScenario.MALFORMED, 'MALFORMED_JSON'),
    (MockScenario.EMPTY, 'EMPTY_RESPONSE'),
    (MockScenario.FAILURE, 'PROVIDER_FAILURE'),
])
def test_every_mock_failure_falls_back_to_a_labeled_template(scenario, rule):
    provider = MockLLMService(scenario)
    source = analysis()
    result = GuidanceEngine(provider).generate(source)
    assert (result.source, result.groundingPassed, result.failedRule) == ('TEMPLATE', False, rule)
    assert result.failureReason and result.providerCalled
    assert [item.message for item in result.items] == [template_message(e) for e in source.estimates]
    assert result.items[0].suggestedQty == '28'
    assert len(provider.calls) == 1  # No automatic retry.


def test_timeout_is_enforced_while_the_provider_is_still_blocking():
    provider = MockLLMService(MockScenario.TIMEOUT, hang_seconds=30)
    started = time.monotonic()
    try:
        result = GuidanceEngine(provider, timeout_seconds=0.2).generate(analysis())
    finally:
        provider.release.set()
    assert time.monotonic() - started < 5
    assert (result.source, result.groundingPassed, result.failedRule) == ('TEMPLATE', False, 'PROVIDER_TIMEOUT')
    assert len(provider.calls) == 1


def test_unconfigured_provider_uses_the_template():
    result = GuidanceEngine(UnavailableWordingProvider()).generate(analysis())
    assert (result.source, result.groundingPassed, result.failedRule) == ('TEMPLATE', False, 'PROVIDER_NOT_CONFIGURED')
    assert result.items


@pytest.mark.parametrize('days', [0, 3, 6])
def test_no_provider_call_without_an_eligible_estimate(days):
    provider = MockLLMService()
    source = analysis(days) if days else build_analysis([], AS_OF, START)
    result = GuidanceEngine(provider).generate(source)
    assert provider.calls == []
    assert (result.status, result.items, result.providerCalled) == ('INSUFFICIENT_HISTORY', [], False)
    assert (result.groundingPassed, result.failedRule, result.provider) == (False, 'INSUFFICIENT_HISTORY', None)
    assert result.stockNotice == STOCK_NOTICE


def test_seven_to_thirteen_dates_get_guidance_without_a_trend_claim():
    result = GuidanceEngine(MockLLMService()).generate(analysis(9))
    assert result.source == 'AI_WORDED'
    assert all(item.trend == 'INSUFFICIENT_DATA' and item.previousAverage is None for item in result.items)
    assert 'trend is' not in result.items[0].message
    assert 'not enough history' in template_message(analysis(9).estimates[0])


def test_provider_receives_only_calculated_evidence():
    provider = MockLLMService()
    GuidanceEngine(provider).generate(analysis())
    request = provider.calls[0]
    for item in request.items:
        assert set(asdict(item)) == {'itemName', 'suggestedQty', 'currentAverage', 'previousAverage',
                                     'trend', 'changePercent', 'windowLoggedDays'}
    assert 'unitPrice' not in request.prompt()
    assert 'saleDate' not in request.prompt()


def test_failures_log_only_a_rule_code(caplog):
    class Leaky:
        name = 'leaky'
        def generate(self, request):
            raise RuntimeError('SECRET-ERROR-TEXT')
    secret_reply = provider_reply('Bread: SECRET-REPLY-TEXT 28 units')
    class Rejected:
        name = 'rejected'
        def generate(self, request):
            return secret_reply
    with caplog.at_level(logging.DEBUG):
        first = GuidanceEngine(Leaky()).generate(analysis())
        second = GuidanceEngine(Rejected()).generate(analysis())
    assert 'SECRET' not in caplog.text and 'Bread' not in caplog.text
    assert 'rule=PROVIDER_FAILURE' in caplog.text
    assert 'SECRET' not in first.model_dump_json() and 'SECRET' not in second.model_dump_json()
    assert second.failedRule == 'MISSING_ITEM'


def test_template_handles_rounded_and_zero_quantities():
    dates = logged_dates(14)
    records = [{'saleDate': day, 'itemName': 'Old', 'quantity': Decimal('1')} for day in dates[:7]]
    records += [{'saleDate': day, 'itemName': 'New', 'quantity': Decimal('1')} for day in dates[7:]]
    records.append({'saleDate': dates[-1], 'itemName': 'Odd', 'quantity': Decimal('10')})
    estimates = {e.itemKey: e for e in build_analysis(records, AS_OF, START).estimates}
    assert 'no quantity is suggested' in template_message(estimates['old'])
    assert 'trending down' in template_message(estimates['old'])
    assert 'about 1.428571' in template_message(estimates['odd'])
    assert 'plan for 10 units' in template_message(estimates['odd'])

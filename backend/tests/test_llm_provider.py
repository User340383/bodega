"""ClaudeLLMService request shape and failure mapping, with a fake client (no paid API, no network).

These tests prove what the adapter sends and how it handles each response type. They do not
prove real-provider wording quality, which needs an authorized key (docs/TRACEABILITY.md).
"""
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.analysis import build_analysis
from app.guidance import GuidanceEngine
from app.llm import (ClaudeLLMService, MockLLMService, ProviderRefused, UnavailableWordingProvider,
                     WRITE_GUIDANCE_TOOL, WordingRequest, get_wording_provider)
from conftest import AS_OF, logged_dates


def analysis():
    records = [{'saleDate': day, 'itemName': 'Bread', 'quantity': Decimal('4'), 'unitPrice': Decimal('9.99')}
               for day in logged_dates(7)]
    return build_analysis(records, AS_OF, AS_OF - timedelta(days=27))


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def fake_client(content, stop_reason='tool_use'):
    messages = FakeMessages(SimpleNamespace(stop_reason=stop_reason, content=content))
    return SimpleNamespace(messages=messages), messages


def tool_use(items):
    return SimpleNamespace(type='tool_use', name='write_guidance', input={'items': items})


def test_request_uses_strict_tool_auto_choice_and_only_calculated_evidence():
    client, messages = fake_client([tool_use([{
        'itemName': 'Bread', 'suggestedQty': '28',
        'message': 'Bread: you sold about 4.0 per logged day over your last 7 logged days. '
                   'Consider having 28 on hand for your next 7 selling days.'}])])
    result = GuidanceEngine(ClaudeLLMService('key', 'claude-haiku-5-5', client=client)).generate(analysis())
    assert (result.source, result.groundingPassed, result.provider) == ('AI_WORDED', True, 'claude')
    call = messages.calls[0]
    assert call['model'] == 'claude-haiku-5-5'
    assert call['tools'] == [WRITE_GUIDANCE_TOOL] and WRITE_GUIDANCE_TOOL['strict'] is True
    assert call['tool_choice'] == {'type': 'auto'}
    assert not {'temperature', 'top_p', 'top_k'} & set(call)  # Rejected by current models.
    sent = json.dumps(call['messages'])
    assert 'Bread' in sent and '9.99' not in sent and AS_OF.isoformat() not in sent  # NFR-713.


@pytest.mark.parametrize('content,stop_reason', [
    ([], 'refusal'),
    ([SimpleNamespace(type='text', text='Bread: buy lots')], 'end_turn'),
    ([tool_use([])], 'max_tokens'),
])
def test_unusable_replies_fall_back_to_standard_advice(content, stop_reason):
    client, _ = fake_client(content, stop_reason)
    result = GuidanceEngine(ClaudeLLMService('key', client=client)).generate(analysis())
    assert (result.source, result.sourceLabel, result.groundingPassed) == ('TEMPLATE', 'Standard advice', False)
    assert result.failedRule == 'PROVIDER_FAILURE'
    assert result.items[0].message.startswith('Bread: you sold about 4.0 per logged day')


def test_refusal_is_raised_before_content_is_read():
    client, _ = fake_client([], 'refusal')
    with pytest.raises(ProviderRefused):
        ClaudeLLMService('key', client=client).generate(WordingRequest('instructions', ()))


def test_ungrounded_claude_wording_is_rejected():
    client, _ = fake_client([tool_use([{'itemName': 'Bread', 'suggestedQty': '28',
                                        'message': 'Bread: consider having 28 on hand. Prices are guaranteed.'}])])
    result = GuidanceEngine(ClaudeLLMService('key', client=client)).generate(analysis())
    assert result.source == 'TEMPLATE' and result.failedRule == 'UNSUPPORTED_CLAIM'


@pytest.mark.parametrize('env,expected', [
    ({'AI_PROVIDER': 'mock'}, MockLLMService),
    ({'AI_PROVIDER': ' Mock '}, MockLLMService),
    ({'AI_PROVIDER': 'claude'}, UnavailableWordingProvider),  # No key: template wording only.
    ({'AI_PROVIDER': 'claude', 'AI_API_KEY': 'sk-test'}, ClaudeLLMService),
    ({}, UnavailableWordingProvider),
])
def test_provider_selection(monkeypatch, env, expected):
    for name in ('AI_PROVIDER', 'AI_API_KEY', 'AI_GUIDANCE_MODEL'):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    provider = get_wording_provider()
    assert isinstance(provider, expected)
    if expected is ClaudeLLMService:
        assert provider.model_name == 'claude-haiku-5-5'
        assert provider.client.max_retries == 0 and provider.client.timeout == 15.0

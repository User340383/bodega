"""NFR-724: 20 forced-timeout trials each for extraction and for guidance. 100% must end in the
FR-211 / FR-505 fallback within the NFR-705 limits, with 0 partial, duplicate or orphaned records.

The providers genuinely hang (they block until released), so the deadline code is what ends each
trial. Deadlines are scaled down (30 s -> 0.2 s, 15 s -> 0.2 s) so 40 trials run in seconds; the
production values themselves are asserted separately. NFR-722 is checked on the way: with the AI
provider down, manual entry, history, analysis and deletion all keep working.
"""
import time
from uuid import uuid4

import pytest

import app.main as main_module
from app import ingestion
from app.database import connect
from app.guidance import PROVIDER_TIMEOUT_SECONDS, GuidanceEngine
from app.ingestion import InferenceService
from app.llm import MockLLMService, MockScenario, get_wording_provider
from app.main import app
from app.vision import MockVisionModel, get_vision_model
from conftest import AS_OF, logged_dates

TRIALS = 20
SCALED_DEADLINE = 0.2


def counts():
    with connect() as conn:
        return conn.execute("""SELECT count(*), count(*) FILTER (WHERE status='AI_UNAVAILABLE'),
            (SELECT count(*) FROM sales_records) FROM submissions""").fetchone()


def test_production_deadlines_are_the_specified_values():
    assert (ingestion.TIMEOUT_SECONDS, ingestion.MAX_ATTEMPTS, ingestion.RETRY_DELAY_SECONDS) == (30.0, 2, 1.0)
    assert PROVIDER_TIMEOUT_SECONDS == 15.0
    # NFR-705: worst case 30 s + 1 s + 30 s = 61 s < 65 s; guidance 15 s < 16 s.
    assert ingestion.TIMEOUT_SECONDS * 2 + ingestion.RETRY_DELAY_SECONDS < 65
    assert PROVIDER_TIMEOUT_SECONDS < 16


@pytest.fixture
def scaled(client, monkeypatch):
    monkeypatch.setattr(main_module, 'InferenceService', lambda model: InferenceService(
        model, timeout_seconds=SCALED_DEADLINE, retry_delay_seconds=0.01))
    monkeypatch.setattr(main_module, 'GuidanceEngine', lambda provider: GuidanceEngine(provider, timeout_seconds=SCALED_DEADLINE))
    return client


def test_twenty_forced_extraction_timeouts_all_fall_back_to_manual_entry(scaled):
    for trial in range(TRIALS):
        model = MockVisionModel(fail_modes=['hang', 'hang'], hang_seconds=5)
        app.dependency_overrides[get_vision_model] = (lambda m: lambda: m)(model)
        started = time.monotonic()
        try:
            response = scaled.post('/api/v1/extract', data={'saleDate': AS_OF.isoformat(), 'text': f'Bread {trial + 1} 1.50'})
        finally:
            model.release.set()
        elapsed = time.monotonic() - started
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body['status'], body['manualEntry'], body['lines']) == ('AI_UNAVAILABLE', True, [])
        assert len(model.calls) == 2  # One retry, no more.
        assert elapsed < 2 * SCALED_DEADLINE + 1.0  # Scaled NFR-705 bound.
    assert counts() == (TRIALS, TRIALS, 0)  # One submission per trial, no partial or orphaned rows.


def test_twenty_forced_guidance_timeouts_all_return_standard_advice(scaled):
    for day in logged_dates(7):
        scaled.post('/api/v1/manual-entries', json={'submissionId': str(uuid4()), 'saleDate': day.isoformat(),
                                                    'lines': [{'itemName': 'Bread', 'quantity': '4', 'unitPrice': '1'}]})
    before = counts()
    for _ in range(TRIALS):
        provider = MockLLMService(MockScenario.TIMEOUT, hang_seconds=5)
        app.dependency_overrides[get_wording_provider] = (lambda p: lambda: p)(provider)
        started = time.monotonic()
        try:
            result = scaled.post('/api/v1/guidance', json={}).json()
        finally:
            provider.release.set()
        assert time.monotonic() - started < SCALED_DEADLINE + 1.0
        assert (result['source'], result['sourceLabel'], result['failedRule']) == ('TEMPLATE', 'Standard advice', 'PROVIDER_TIMEOUT')
        assert len(provider.calls) == 1  # Never retried.
        assert result['items'][0]['suggestedQty'] == '28'  # Same numbers as without AI.
    assert counts() == before  # Guidance never changes sales history.


def test_non_ai_functions_work_with_the_ai_service_down(client):
    """NFR-722 (AC-05/07/12/18/21 functions) with AI_PROVIDER unset: no extraction or wording provider."""
    sid = str(uuid4())
    for index, day in enumerate(logged_dates(7)):
        response = client.post('/api/v1/manual-entries', json={
            'submissionId': sid if index == 0 else str(uuid4()), 'saleDate': day.isoformat(),
            'lines': [{'itemName': 'Bread', 'quantity': '4', 'unitPrice': '1.50'}]})
        assert response.status_code == 201
    assert len(client.get('/api/v1/sales').json()['records']) == 7                    # history
    assert client.get('/api/v1/analysis').json()['estimates'][0]['suggestedQty'] == '28'  # analysis
    advice = client.post('/api/v1/guidance', json={}).json()
    assert advice['source'] == 'TEMPLATE' and advice['failedRule'] == 'PROVIDER_NOT_CONFIGURED'
    capture = client.post('/api/v1/extract', data={'saleDate': AS_OF.isoformat(), 'text': 'Bread 3'}).json()
    assert capture['status'] == 'AI_UNAVAILABLE' and capture['manualEntry'] is True  # manual path
    assert client.delete('/api/v1/submissions/' + sid).status_code == 204              # deletion

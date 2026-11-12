"""End-to-end from the VISION-OUTPUT PERSISTENCE BOUNDARY to guidance.

Starts from MockVisionModel's draft, persisted with repository.create_submission and
update_status. It does not start at image upload: production extraction is not implemented
on main, so POST /api/v1/extract and José's future adapter are outside this test.
"""
from app import repository
from app.llm import MockLLMService, get_wording_provider
from app.main import app
from conftest import logged_dates
from mock_vision import MockVisionModel


def persist_vision_draft(sale_date, model):
    """The valid lifecycle: UPLOADED -> CLASSIFYING -> NEEDS_VERIFICATION with a draft."""
    submission_id = repository.create_submission(sale_date)
    repository.update_status(submission_id, 'CLASSIFYING')
    repository.update_status(submission_id, 'NEEDS_VERIFICATION', draft_payload=model.extract(b'synthetic-image'))
    return submission_id


def confirm_body(submission_id, lines, **line_overrides):
    return {'submissionId': str(submission_id), 'lines': [{
        'draftLineId': line['lineId'], 'itemName': line['itemName'], 'quantity': line['quantity'],
        'unitPrice': line['unitPrice'], **line_overrides,
    } for line in lines]}


def test_vision_draft_to_confirmation_analysis_and_guidance(client):
    provider = MockLLMService()
    app.dependency_overrides[get_wording_provider] = lambda: provider
    dates = logged_dates(14)

    for position, sale_date in enumerate(dates):
        quantity = '4' if position >= 7 else '2'
        model = MockVisionModel([('Bread', quantity, '1.50', 'MEDIUM')])
        submission_id = persist_vision_draft(sale_date, model)

        draft = client.get(f'/api/v1/submissions/{submission_id}').json()
        assert (draft['status'], draft['lines'][0]['lineId']) == ('NEEDS_VERIFICATION', 'line-0')

        # Nothing counts until the owner confirms, and uncertain fields need verification.
        assert client.get('/api/v1/analysis').json()['loggedDays'] == position
        assert client.post('/api/v1/confirm', json=confirm_body(submission_id, draft['lines'])).status_code == 400

        body = confirm_body(submission_id, draft['lines'], verifiedFields=['quantity'])
        first = client.post('/api/v1/confirm', json=body)
        assert first.status_code == 201, first.text
        assert model.calls == 1

        # Idempotent: a repeat returns the same records and cannot double-count sales.
        repeat = client.post('/api/v1/confirm', json=body)
        assert repeat.status_code == 200 and repeat.json()['recordIds'] == first.json()['recordIds']

    records = client.get('/api/v1/sales').json()['records']
    assert len(records) == 14
    assert {record['source'] for record in records} == {'AI_EXTRACTED'}  # Provenance is preserved.

    analysis = client.get('/api/v1/analysis').json()
    estimate = analysis['estimates'][0]
    assert (analysis['status'], estimate['previousAverage'], estimate['currentAverage']) == ('READY', '2', '4')
    assert (estimate['trend'], estimate['suggestedQty']) == ('UP', '28')

    guidance = client.post('/api/v1/guidance', json={'asOf': analysis['asOf']}).json()
    assert (guidance['source'], guidance['groundingPassed'], guidance['asOf']) == ('AI_WORDED', True, analysis['asOf'])
    assert guidance['items'][0]['suggestedQty'] == '28'
    assert len(provider.calls) == 1


def test_owner_corrections_and_added_manual_rows_reach_the_analysis(client):
    dates = logged_dates(7)
    for sale_date in dates:
        model = MockVisionModel([('Bred', '1', '1.50', 'HIGH')])  # A misread label.
        submission_id = persist_vision_draft(sale_date, model)
        draft = client.get(f'/api/v1/submissions/{submission_id}').json()
        body = confirm_body(submission_id, draft['lines'], itemName='Bread', quantity='3')
        body['lines'].append({'source': 'MANUAL', 'itemName': 'Milk', 'quantity': '2', 'unitPrice': '1.00'})
        assert client.post('/api/v1/confirm', json=body).status_code == 201

    estimates = {e['itemKey']: e for e in client.get('/api/v1/analysis').json()['estimates']}
    assert set(estimates) == {'bread', 'milk'}  # The misread label never became an item.
    assert estimates['bread']['currentTotal'] == '21'
    assert estimates['milk']['currentTotal'] == '14'
    sources = {r['itemName']: r['source'] for r in client.get('/api/v1/sales').json()['records']}
    assert sources == {'Bread': 'AI_EXTRACTED', 'Milk': 'MANUAL'}

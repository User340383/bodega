"""SRS (Milestone 2 v2.0) acceptance criteria, using the SRS's own data, through HTTP and PostgreSQL.

AI responses are stubbed, as SRS section 4 permits ("AI responses, including confidence scores and
timeouts, may be stubbed in acceptance tests"). These tests do not measure real extraction accuracy.

Superseded by Milestone 3 section 3 and tested in their M3 form elsewhere: AC-07, AC-08, AC-12
(7-logged-day average replaces the all-history average; test_insights_api, test_analysis_math),
AC-09, AC-10, AC-11, AC-22, AC-23 (template "Standard advice" replaces withholding; test_grounding,
test_insights_api, test_resilience), AC-20 (30-day purge replaces 24 h; test_final_contract).
"""
import io
import struct
import zlib
from uuid import uuid4

import pytest
from PIL import Image

from app import repository
from app.database import connect
from app.main import app
from app.privacy import MAX_IMAGE_BYTES
from app.storage import InMemoryObjectStorage, get_object_storage
from app.vision import ExtractedLine, ExtractionResult, MockVisionModel, get_vision_model
from conftest import AS_OF

DATE = AS_OF.isoformat()


def stub(result=None, **kwargs):
    model = MockVisionModel(canned=result, **kwargs)
    storage = InMemoryObjectStorage()
    app.dependency_overrides[get_vision_model] = lambda: model
    app.dependency_overrides[get_object_storage] = lambda: storage
    return model, storage


def line(name, quantity, price, qc, pc, nc=0.95):
    return ExtractedLine(itemName=name, quantity=quantity, unitPrice=price,
                         itemNameConfidence=nc, quantityConfidence=qc, unitPriceConfidence=pc)


def records():
    with connect() as conn:
        return conn.execute('SELECT count(*) FROM sales_records').fetchone()[0]


def png_of_exact_size(size):
    """A decodable PNG padded with a private ancillary chunk to exactly `size` bytes."""
    out = io.BytesIO()
    Image.new('RGB', (64, 64), 'white').save(out, format='PNG')
    data = out.getvalue()
    iend = data.rindex(b'IEND') - 4
    padding = size - len(data) - 12
    chunk_type = b'prVt'  # Ancillary and private: decoders skip it.
    body = b'\0' * padding
    chunk = struct.pack('>I', padding) + chunk_type + body + struct.pack('>I', zlib.crc32(chunk_type + body))
    result = data[:iend] + chunk + data[iend:]
    assert len(result) == size
    return result


# ---- US-01 ----------------------------------------------------------------------------------
def test_ac01_accepts_supported_text_and_image(client):
    stub()
    text = client.post('/api/v1/extract', data={'saleDate': DATE, 'text': 'Sold 3 bread at 2 each'})
    assert text.status_code == 200 and text.json()['saleDate'] == DATE
    out = io.BytesIO()
    Image.new('RGB', (40, 40), 'white').save(out, format='JPEG')
    image = client.post('/api/v1/extract', data={'saleDate': DATE}, files={'file': ('log.jpg', out.getvalue(), 'image/jpeg')})
    assert image.status_code == 200 and image.json()['inputType'] == 'IMAGE'


def test_ac02_boundary_image_of_exactly_10485760_bytes_is_accepted_and_one_more_is_rejected(client):
    model, _ = stub()
    exact = png_of_exact_size(MAX_IMAGE_BYTES)
    accepted = client.post('/api/v1/extract', data={'saleDate': DATE}, files={'file': ('log.png', exact, 'image/png')})
    assert accepted.status_code == 200, accepted.text
    too_big = exact[:-12] + b'\0' + exact[-12:]
    assert len(too_big) == MAX_IMAGE_BYTES + 1
    calls = len(model.calls)
    rejected = client.post('/api/v1/extract', data={'saleDate': DATE}, files={'file': ('log.png', too_big, 'image/png')})
    assert rejected.status_code == 400 and 'file' in rejected.json()['error']['fields']
    assert len(model.calls) == calls  # No extraction request.


# ---- US-02 ----------------------------------------------------------------------------------
def test_ac03_correct_a_draft_before_saving(client):
    stub(ExtractionResult(overallConfidence=0.9, lines=[
        line('bread', 3, 2, 0.95, 0.95), ExtractedLine(itemName='milk', quantity=None, unitPrice=1,
                                                       itemNameConfidence=0.95, quantityConfidence=0.95, unitPriceConfidence=0.95)]))
    draft = client.post('/api/v1/extract', data={'saleDate': DATE, 'text': 'bread 3 2, milk ? 1'}).json()
    bread, milk = draft['lines']
    assert milk['quantity'] is None and milk['bands']['quantity'] == 'LOW'  # Flagged, cannot confirm as is.
    assert client.get('/api/v1/sales').json()['records'] == []             # Not in history before confirming.
    # Correct milk to 2, change bread price to 2.50, then remove the milk row.
    response = client.post('/api/v1/confirm', json={'submissionId': draft['submissionId'], 'lines': [
        {'draftLineId': bread['lineId'], 'itemName': 'bread', 'quantity': '3', 'unitPrice': '2.50'}]})
    assert response.status_code == 201
    saved = client.get('/api/v1/sales').json()['records']
    assert [(r['itemName'], r['quantity'], r['unitPrice']) for r in saved] == [('bread', '3', '2.50')]


@pytest.mark.parametrize('changes', [{'itemName': ' '}, {'quantity': '0'}, {'quantity': '-1'}, {'quantity': 'abc'},
                                     {'unitPrice': '-1'}, {'unitPrice': 'abc'}])
def test_ac04_invalid_fields_block_confirmation(client, changes):
    sid = repository.create_submission(AS_OF)
    repository.update_status(sid, 'CLASSIFYING')
    repository.update_status(sid, 'AI_UNAVAILABLE')
    row = {'itemName': 'Bread', 'quantity': '1', 'unitPrice': '0', **changes}
    response = client.post('/api/v1/confirm', json={'submissionId': str(sid), 'lines': [row]})
    assert response.status_code == 400 and response.json()['error']['fields']
    assert client.post('/api/v1/confirm', json={'submissionId': str(sid), 'lines': []}).status_code == 400
    ok = client.post('/api/v1/confirm', json={'submissionId': str(sid), 'lines': [
        {'itemName': 'Bread', 'quantity': '1', 'unitPrice': '0'}]})
    assert ok.status_code == 201  # Nonblank item, quantity 1, price 0 passes.


@pytest.mark.parametrize('kwargs,status', [
    ({'fail_modes': ['fatal']}, 'AI_UNAVAILABLE'),
    ({'fail_modes': ['retryable', 'retryable']}, 'AI_UNAVAILABLE'),
    ({'canned': ExtractionResult(overallConfidence=0.9, lines=[])}, 'REJECTED'),
])
def test_ac04_extraction_failures_show_a_message_and_save_nothing(client, kwargs, status):
    model = MockVisionModel(**kwargs)
    app.dependency_overrides[get_vision_model] = lambda: model
    body = client.post('/api/v1/extract', data={'saleDate': DATE, 'text': 'Bread 3'}).json()
    assert body['status'] == status and body['message'] and body['lines'] == []
    assert records() == 0


def test_ac14_high_confidence_prefilled_and_uncertain_value_requires_verification(client):
    stub(ExtractionResult(overallConfidence=0.90, lines=[line('bread', 3, '2.00', qc=0.95, pc=0.72)]))
    draft = client.post('/api/v1/extract', data={'saleDate': DATE, 'text': 'bread 3 2.00'}).json()
    bread = draft['lines'][0]
    assert bread['bands'] == {'itemName': 'HIGH', 'quantity': 'HIGH', 'unitPrice': 'MEDIUM'}
    assert (bread['quantity'], bread['unitPrice']) == ('3', '2')
    base = {'draftLineId': bread['lineId'], 'itemName': 'bread', 'quantity': '3', 'unitPrice': '2.00'}
    blocked = client.post('/api/v1/confirm', json={'submissionId': draft['submissionId'], 'lines': [base]})
    assert blocked.status_code == 400  # Not verified.
    saved = client.post('/api/v1/confirm', json={'submissionId': draft['submissionId'],
                                                'lines': [{**base, 'verifiedFields': ['unitPrice']}]})
    assert saved.status_code == 201
    record = client.get('/api/v1/sales').json()['records'][0]
    assert (record['quantity'], record['unitPrice']) == ('3', '2.00')


def test_ac15_low_or_missing_confidence_and_band_boundaries(client):
    stub(ExtractionResult(overallConfidence=0.80, lines=[line('milk', 2, '1.50', qc=0.45, pc=None),
                                                         line('a', 1, 1, qc=0.85, pc=0.60, nc=0.59)]))
    draft = client.post('/api/v1/extract', data={'saleDate': DATE, 'text': 'milk 2 1.50'}).json()
    milk, boundary = draft['lines']
    assert milk['quantity'] is None and milk['bands']['quantity'] == 'LOW'
    assert milk['unitPrice'] == '1.50' and milk['bands']['unitPrice'] == 'UNSCORED'
    assert boundary['bands'] == {'quantity': 'HIGH', 'unitPrice': 'MEDIUM', 'itemName': 'LOW'}  # 0.85 / 0.60 / 0.59
    row = {'draftLineId': milk['lineId'], 'itemName': 'milk', 'quantity': '2', 'unitPrice': '1.50'}
    sid = draft['submissionId']
    assert client.post('/api/v1/confirm', json={'submissionId': sid, 'lines': [row]}).status_code == 400  # Price unverified.
    assert client.post('/api/v1/confirm', json={'submissionId': sid, 'lines': [
        {**row, 'verifiedFields': ['unitPrice']}]}).status_code == 201


@pytest.mark.parametrize('legibility,rows', [(0.35, 0), (0.40, 1)])
def test_ac16_unreadable_image_and_the_040_boundary(client, legibility, rows):
    stub(ExtractionResult(overallConfidence=legibility, lines=[line('bread', 3, 2, 0.95, 0.95)]))
    out = io.BytesIO()
    Image.new('RGB', (40, 40), 'white').save(out, format='JPEG')
    body = client.post('/api/v1/extract', data={'saleDate': DATE}, files={'file': ('log.jpg', out.getvalue(), 'image/jpeg')}).json()
    assert len(body['lines']) == rows
    if not rows:
        assert body['status'] == 'REJECTED' and body['manualEntry'] is True
        assert body['message'].startswith('Image could not be read') and 'flat' in body['message']
    assert records() == 0


# ---- US-03 ----------------------------------------------------------------------------------
def test_ac05_persist_confirmed_records_once(client):
    sid = repository.create_submission(AS_OF)
    repository.update_status(sid, 'CLASSIFYING')
    repository.update_status(sid, 'AI_UNAVAILABLE')
    body = {'submissionId': str(sid), 'lines': [{'itemName': 'Bread', 'quantity': '3', 'unitPrice': '2'},
                                                {'itemName': 'Milk', 'quantity': '1', 'unitPrice': '1.25'}]}
    first, again = client.post('/api/v1/confirm', json=body), client.post('/api/v1/confirm', json=body)
    assert (first.status_code, again.status_code) == (201, 200) and first.json()['recordIds'] == again.json()['recordIds']
    saved = client.get('/api/v1/sales').json()['records']
    assert len(saved) == 2 and len({r['recordId'] for r in saved}) == 2
    assert {(r['submissionId'], r['saleDate'], r['itemName'], r['quantity'], r['unitPrice']) for r in saved} == {
        (str(sid), DATE, 'Bread', '3', '2'), (str(sid), DATE, 'Milk', '1', '1.25')}


def test_ac06_failed_save_persists_nothing_and_keeps_earlier_records(client):
    earlier = {'submissionId': str(uuid4()), 'saleDate': DATE, 'lines': [{'itemName': 'Eggs', 'quantity': '12', 'unitPrice': '0.25'}]}
    assert client.post('/api/v1/manual-entries', json=earlier).status_code == 201
    sid = repository.create_submission(AS_OF)
    repository.update_status(sid, 'CLASSIFYING')
    repository.update_status(sid, 'AI_UNAVAILABLE')
    with connect() as conn:
        conn.execute("CREATE FUNCTION fail_second() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                     "IF NEW.line_no = 2 THEN RAISE EXCEPTION 'simulated storage failure'; END IF; RETURN NEW; END $$")
        conn.execute('CREATE TRIGGER fail_second BEFORE INSERT ON sales_records FOR EACH ROW EXECUTE FUNCTION fail_second()')
    response = client.post('/api/v1/confirm', json={'submissionId': str(sid), 'lines': [
        {'itemName': 'Bread', 'quantity': '3', 'unitPrice': '2'}, {'itemName': 'Milk', 'quantity': '1', 'unitPrice': '1'}]})
    assert response.status_code == 500 and 'could not be saved' in response.json()['error']['message']
    assert [r['itemName'] for r in client.get('/api/v1/sales').json()['records']] == ['Eggs']


# ---- US-07, US-08, US-09 --------------------------------------------------------------------
def test_ac13_no_records_shows_no_data_without_fabricated_values(client):
    result = client.get('/api/v1/analysis').json()
    assert result['status'] == 'NO_DATA' and result['estimates'] == []
    assert client.post('/api/v1/guidance', json={}).status_code == 409  # No recommendation is fabricated.


def test_ac17_timeout_twice_retains_text_and_date_and_offers_manual_entry(client, monkeypatch):
    import app.main as main_module
    from app.ingestion import InferenceService
    monkeypatch.setattr(main_module, 'InferenceService', lambda m: InferenceService(m, retry_delay_seconds=0))
    model, _ = stub(fail_modes=['timeout', 'timeout'])
    body = client.post('/api/v1/extract', data={'saleDate': DATE, 'text': 'Bread 3 2.00'}).json()
    assert len(model.calls) == 2  # Retried automatically once.
    assert body['message'].startswith('AI reading is temporarily unavailable')
    assert (body['manualEntry'], body['maskedText'], body['saleDate']) == (True, 'Bread 3 2.00', DATE)
    assert records() == 0
    retried = MockVisionModel(fail_modes=['timeout'])
    app.dependency_overrides[get_vision_model] = lambda: retried
    assert len(client.post('/api/v1/extract', data={'saleDate': DATE, 'text': 'Bread 3 2.00'}).json()['lines']) == 1


def test_ac18_manual_entry_saves_with_no_ai_request(client):
    model, _ = stub()
    sid = str(uuid4())
    saved = client.post('/api/v1/manual-entries', json={'submissionId': sid, 'saleDate': DATE,
                                                       'lines': [{'itemName': 'bread', 'quantity': '4', 'unitPrice': '2.00'}]})
    assert saved.status_code == 201 and model.calls == []
    assert [(r['itemName'], r['quantity'], r['unitPrice']) for r in client.get('/api/v1/sales').json()['records']] == [
        ('bread', '4', '2.00')]
    zero = client.post('/api/v1/manual-entries', json={'submissionId': str(uuid4()), 'saleDate': DATE,
                                                      'lines': [{'itemName': 'bread', 'quantity': '0', 'unitPrice': '2.00'}]})
    assert zero.status_code == 400 and 'lines.0.quantity' in zero.json()['error']['fields']


def test_ac19_personal_data_is_removed_before_ai_processing(client):
    model, storage = stub()
    client.post('/api/v1/extract', data={'saleDate': DATE, 'text': 'Sold 3 bread to Ana 555-123-4567, ana@mail.com'})
    client.post('/api/v1/extract', data={'saleDate': DATE, 'text': 'Sold 120 bread'})
    assert [payload for kind, payload in model.calls] == ['Sold 3 bread to Ana [removed], [removed]', 'Sold 120 bread']
    gps = Image.new('RGB', (40, 40), 'white')
    exif = gps.getexif()
    exif[0x0110] = 'PhoneModel'      # Device.
    exif[0x0132] = '2026:04:30 10:00:00'  # Timestamp.
    exif[0x8825] = {2: (18.0, 28.0, 0.0), 1: 'N'}  # GPS.
    out = io.BytesIO()
    gps.save(out, format='JPEG', exif=exif)
    original = Image.open(io.BytesIO(out.getvalue())).getexif()
    assert {0x0110, 0x0132, 0x8825} <= set(original)  # The upload really carries device, time and GPS.
    client.post('/api/v1/extract', data={'saleDate': DATE}, files={'file': ('log.jpg', out.getvalue(), 'image/jpeg')})
    sent = model.calls[-1][1]
    assert dict(Image.open(io.BytesIO(sent)).getexif()) == {}
    stored = next(iter(storage.objects.values()))[0]
    assert dict(Image.open(io.BytesIO(stored)).getexif()) == {}


def test_ac21_delete_all_stored_sales_data(client):
    for index in range(5):
        client.post('/api/v1/manual-entries', json={'submissionId': str(uuid4()), 'saleDate': DATE,
                                                   'lines': [{'itemName': f'Item {index}', 'quantity': '1', 'unitPrice': '1'}]})
    for wrong in ('delete', 'DELETE ALL', ''):
        response = client.post('/api/v1/data/delete-all', json={'confirmation': wrong})
        assert response.status_code == 400
    assert len(client.get('/api/v1/sales').json()['records']) == 5  # Other text deleted nothing.
    result = client.post('/api/v1/data/delete-all', json={'confirmation': 'DELETE'})
    assert result.status_code == 200 and result.json()['message'] == '5 records deleted'
    assert client.get('/api/v1/sales').json()['records'] == []
    assert client.get('/api/v1/analysis').json()['status'] == 'NO_DATA'


def test_ac21_delete_all_also_removes_raw_input_and_waits_for_processing(client):
    _, storage = stub()
    out = io.BytesIO()
    Image.new('RGB', (40, 40), 'white').save(out, format='JPEG')
    client.post('/api/v1/extract', data={'saleDate': DATE}, files={'file': ('log.jpg', out.getvalue(), 'image/jpeg')})
    assert len(storage.objects) == 1
    processing = repository.create_submission(AS_OF)
    refused = client.post('/api/v1/data/delete-all', json={'confirmation': 'DELETE'})
    assert refused.status_code == 409 and len(storage.objects) == 1  # Nothing deleted while one is being read.
    repository.update_status(processing, 'AI_UNAVAILABLE')
    deleted = client.post('/api/v1/data/delete-all', json={'confirmation': 'DELETE'}).json()
    assert deleted == {'recordsDeleted': 0, 'submissionsDeleted': 2, 'message': '0 records deleted'}
    assert storage.objects == {}
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM submissions').fetchone()[0] == 0

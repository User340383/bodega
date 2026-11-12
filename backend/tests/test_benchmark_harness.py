"""The NFR-751/752 benchmark harness itself (scoring and the end-to-end run with the mock model).

This proves the harness measures what the SRS asks. It is NOT a benchmark result: the real
targets need 20 real sales-log photos and an authorized provider key (docs/TRACEABILITY.md).
"""
import io
import json

from PIL import Image

from benchmarks.extraction_benchmark import run, score_page
from app.vision import ExtractedLine, ExtractionResult, MockVisionModel


def draft(*lines, status='NEEDS_VERIFICATION'):
    return {'status': status, 'payload': {'lines': list(lines)}}


def high(name, quantity, price):
    return {'itemName': name, 'quantity': quantity, 'unitPrice': price,
            'bands': {'itemName': 'HIGH', 'quantity': 'HIGH', 'unitPrice': 'HIGH'}}


def test_scoring_counts_only_high_fields_and_compares_exactly():
    truth = {'lines': [{'itemName': 'Bread', 'quantity': '3', 'unitPrice': '1.50'}]}
    line = high(' bread ', '3.0', '1.5')
    assert score_page(draft(line), truth) == (3, 3, None)
    wrong = {**high('Bread', '4', '1.50'), 'bands': {'itemName': 'HIGH', 'quantity': 'HIGH', 'unitPrice': 'MEDIUM'}}
    assert score_page(draft(wrong), truth) == (2, 1, None)


def test_unreadable_pages_pass_only_without_rows():
    assert score_page(draft(status='REJECTED'), {'unreadable': True}) == (0, 0, True)
    assert score_page(draft(high('Bread', '1', '1')), {'unreadable': True}) == (0, 0, False)


def test_end_to_end_run_with_the_mock_model(tmp_path):
    for name in ('page01.png', 'blur01.png'):
        out = io.BytesIO()
        Image.new('RGB', (20, 20), 'white').save(out, format='PNG')
        (tmp_path / name).write_bytes(out.getvalue())
    (tmp_path / 'truth.json').write_text(json.dumps({
        'page01.png': {'lines': [{'itemName': 'Bread', 'quantity': '3', 'unitPrice': '1.50'}]},
        'blur01.png': {'unreadable': True}}))
    canned = MockVisionModel(canned=ExtractionResult(overallConfidence=0.9, lines=[ExtractedLine(
        itemName='Bread', quantity=3, unitPrice=1.5, itemNameConfidence=0.9, quantityConfidence=0.9,
        unitPriceConfidence=0.9)]))
    report = run(tmp_path, canned)
    assert report['NFR-751'] == {'highFields': 3, 'highCorrect': 3, 'precision': 1.0, 'target': 0.95, 'passed': True}
    # A model that reads rows from the unreadable page fails NFR-752, and fewer than 10 such pages never pass.
    assert report['NFR-752']['noRows'] == 0 and report['NFR-752']['passed'] is False

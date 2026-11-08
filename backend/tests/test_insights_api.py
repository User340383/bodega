"""HTTP and PostgreSQL integration for /analysis and /guidance in disposable schemas.

Sales enter through the real lifecycle and POST /api/v1/confirm wherever practical.
"""
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

import app.main as main_module
from app import repository
from app.database import connect
from app.guidance import GuidanceEngine, STOCK_NOTICE
from app.llm import MockLLMService, MockScenario, get_wording_provider
from app.main import app
from conftest import AS_OF, logged_dates


def confirm(client, sale_date, lines):
    """lines: (item, quantity) pairs, up to the confirmation limit of 100 per submission."""
    sid = repository.create_submission(sale_date)
    repository.update_status(sid, 'CLASSIFYING')
    repository.update_status(sid, 'AI_UNAVAILABLE')
    body = {'submissionId': str(sid), 'lines': [
        {'itemName': item, 'quantity': str(quantity), 'unitPrice': '1.00'} for item, quantity in lines
    ]}
    if sale_date > AS_OF:
        # The API clock is fixed at AS_OF, where a later date is in the future; record it through
        # the repository as if it had been confirmed on that later business day.
        repository.save_sales_records(main_module.ConfirmRequest(**body), today=sale_date)
        return sid
    response = client.post('/api/v1/confirm', json=body)
    assert response.status_code == 201, response.text
    return sid


def history(client, dates, item='Bread', quantity='1'):
    for day in dates:
        confirm(client, day, [(item, quantity)])


def analysis(client, **params):
    response = client.get('/api/v1/analysis', params=params)
    assert response.status_code == 200, response.text
    return response.json()


def use_provider(provider):
    app.dependency_overrides[get_wording_provider] = lambda: provider
    return provider


def test_no_data(client):
    result = analysis(client)
    assert result['status'] == 'NO_DATA'
    assert (result['asOf'], result['historyStart'], result['historyEnd']) == ('2026-04-30', '2026-04-03', '2026-04-30')
    assert (result['loggedDays'], result['requiredDays'], result['trendRequiredDays']) == (0, 7, 14)
    assert result['estimates'] == [] and result['currentWindow'] is None


def test_insufficient_history_exposes_only_observed_sales(client):
    history(client, logged_dates(6))
    result = analysis(client)
    assert (result['status'], result['loggedDays']) == ('INSUFFICIENT_HISTORY', 6)
    assert '1 more needed' in result['statusMessage']
    estimate = result['estimates'][0]
    assert estimate['currentAverage'] is None and estimate['suggestedQty'] is None
    assert estimate['trend'] == 'INSUFFICIENT_DATA'
    assert all(point['movingAverage'] is None for point in estimate['series'])
    assert [point['quantity'] for point in estimate['series']] == ['1'] * 6


def test_seven_dates_give_an_estimate_but_no_fabricated_trend(client):
    history(client, logged_dates(7), quantity='2.5')
    result = analysis(client)
    assert (result['status'], result['previousWindow']) == ('ESTIMATE_ONLY', None)
    assert result['currentWindow'] == {'start': '2026-04-24', 'end': '2026-04-30', 'loggedDays': 7}
    estimate = result['estimates'][0]
    assert (estimate['currentAverage'], estimate['suggestedQty']) == ('2.5', '18')  # ceil(17.5)
    assert (estimate['previousAverage'], estimate['trend'], estimate['changePercent']) == (None, 'INSUFFICIENT_DATA', None)
    assert [p['movingAverage'] for p in estimate['series']] == [None] * 6 + ['2.5']


def test_thirteen_dates_still_have_no_trend(client):
    history(client, logged_dates(13))
    result = analysis(client)
    assert result['status'] == 'ESTIMATE_ONLY'
    assert result['estimates'][0]['trend'] == 'INSUFFICIENT_DATA'


def test_required_worked_example_through_the_api(client):
    dates = logged_dates(14)
    history(client, dates[:7], quantity='2')
    history(client, dates[7:], quantity='4')
    result = analysis(client)
    assert result['status'] == 'READY'
    assert result['previousWindow'] == {'start': '2026-04-17', 'end': '2026-04-23', 'loggedDays': 7}
    estimate = result['estimates'][0]
    assert (estimate['previousAverage'], estimate['currentAverage']) == ('2', '4')
    assert (estimate['trend'], estimate['suggestedQty']) == ('UP', '28')
    averages = [p['movingAverage'] for p in estimate['series']]
    assert averages[:6] == [None] * 6
    assert averages[6:] == ['2', '2.285714', '2.571429', '2.857143', '3.142857', '3.428571', '3.714286', '4']
    assert estimate['series'][7]['movingAverageRounded'] and not estimate['series'][6]['movingAverageRounded']


def test_missing_item_on_a_logged_date_counts_as_zero(client):
    dates = logged_dates(7)
    for position, day in enumerate(dates):
        lines = [('Milk', '1')]
        if position % 2 == 0:  # Bread appears on 4 of the 7 logged dates.
            lines.append(('Bread', '7'))
        confirm(client, day, lines)
    bread = next(e for e in analysis(client)['estimates'] if e['itemKey'] == 'bread')
    assert bread['currentTotal'] == '28'
    assert bread['currentAverage'] == '4'  # 28/7, not 28/4.
    assert bread['currentDaysWithSales'] == 4
    assert [p['observed'] for p in bread['series']] == [True, False, True, False, True, False, True]
    assert [p['quantity'] for p in bread['series']] == ['7', '0', '7', '0', '7', '0', '7']


def test_dates_with_no_records_stay_unknown_rather_than_zero(client):
    dates = logged_dates(7, step=3)  # Irregular: every third calendar date.
    history(client, dates, quantity='3')
    result = analysis(client)
    assert result['loggedDays'] == 7
    assert result['loggedDates'] == [day.isoformat() for day in dates]
    estimate = result['estimates'][0]
    assert estimate['currentAverage'] == '3'  # Far lower if unlogged calendar dates counted as zero.
    assert len(estimate['series']) == 7
    assert [p['calendarDaysSkipped'] for p in estimate['series']] == [0, 2, 2, 2, 2, 2, 2]


def test_same_date_rows_and_submissions_aggregate_with_label_normalization_and_fractions(client):
    dates = logged_dates(7)
    for day in dates:
        confirm(client, day, [(' bread ', '1.25'), ('BREAD', '0.5')])
        confirm(client, day, [('Bread', '0.25')])
    result = analysis(client)
    assert len(result['estimates']) == 1
    estimate = result['estimates'][0]
    assert estimate['itemKey'] == 'bread'
    assert estimate['series'][0]['quantity'] == '2'
    assert (estimate['currentAverage'], estimate['suggestedQty']) == ('2', '14')
    assert result['loggedDays'] == 7


def test_inclusive_28_day_bounds_and_as_of_filtering(client):
    first_inside = AS_OF - timedelta(days=27)
    history(client, [AS_OF - timedelta(days=28)], quantity='1000')  # Just outside.
    history(client, [first_inside], quantity='5')
    history(client, [AS_OF + timedelta(days=1)], quantity='1000')  # After asOf, though not future.
    history(client, [AS_OF], quantity='2')
    result = analysis(client)
    assert result['loggedDates'] == [first_inside.isoformat(), AS_OF.isoformat()]
    assert [p['quantity'] for p in result['estimates'][0]['series']] == ['5', '2']
    # The window moves with asOf, so the date that was just outside is now its first day.
    earlier = analysis(client, asOf=(AS_OF - timedelta(days=1)).isoformat())
    assert earlier['asOf'] == '2026-04-29'
    assert earlier['historyStart'] == '2026-04-02'
    assert earlier['loggedDates'] == ['2026-04-02', first_inside.isoformat()]


def test_history_older_than_the_window_cannot_satisfy_requirements(client):
    history(client, logged_dates(7, end=AS_OF - timedelta(days=28)))
    assert analysis(client)['status'] == 'NO_DATA'


def test_more_than_100_input_records_are_all_analyzed(client):
    dates = logged_dates(7)
    for day in dates:
        confirm(client, day, [('Bread', '1')] * 30)
    assert len(client.get('/api/v1/sales').json()['records']) == 210  # The 28-day ledger is not paged.
    estimate = analysis(client)['estimates'][0]
    assert estimate['currentTotal'] == '210'
    assert (estimate['currentAverage'], estimate['suggestedQty']) == ('30', '210')


def test_only_the_latest_14_of_more_dates_drive_the_comparison(client):
    dates = logged_dates(20)
    history(client, dates[:6], quantity='100')
    history(client, dates[6:13], quantity='2')
    history(client, dates[13:], quantity='2.2')  # Exactly +10%.
    result = analysis(client)
    assert result['loggedDays'] == 20
    estimate = result['estimates'][0]
    assert (estimate['previousAverage'], estimate['currentAverage'], estimate['trend']) == ('2', '2.2', 'STABLE')
    assert estimate['changePercent'] == '10'


@pytest.mark.parametrize('prior,current,trend', [('10', '11', 'STABLE'), ('10', '9', 'STABLE'), ('10', '11.01', 'UP'), ('10', '8.99', 'DOWN')])
def test_threshold_trends_through_the_api(client, prior, current, trend):
    dates = logged_dates(14)
    history(client, dates[:7], quantity=prior)
    history(client, dates[7:], quantity=current)
    assert analysis(client)['estimates'][0]['trend'] == trend


def test_item_absent_from_prior_window_is_up_with_no_percentage(client):
    dates = logged_dates(14)
    history(client, dates[:7], item='Milk')
    history(client, dates[7:], item='Bread')
    estimates = {e['itemKey']: e for e in analysis(client)['estimates']}
    assert (estimates['bread']['previousAverage'], estimates['bread']['trend'], estimates['bread']['changePercent']) == ('0', 'UP', None)
    assert (estimates['milk']['currentAverage'], estimates['milk']['trend'], estimates['milk']['suggestedQty']) == ('0', 'DOWN', '0')


def test_exact_values_survive_postgresql_round_trip(client):
    # The largest quantity FR-202 allows (99,999.99) on every date: no float rounding anywhere.
    history(client, logged_dates(7), quantity='99999.99')
    estimate = analysis(client)['estimates'][0]
    assert estimate['currentTotal'] == '699999.93'
    assert estimate['currentAverage'] == '99999.99'
    assert estimate['currentAverageDisplay'] == '100000.0'
    assert estimate['suggestedQty'] == '700000'
    assert isinstance(estimate['suggestedQty'], str)


def test_repeating_average_is_exact_and_displayed_to_one_decimal(client):
    dates = logged_dates(7)
    history(client, dates[:6], quantity='1')
    history(client, dates[6:], quantity='4.01')
    estimate = analysis(client)['estimates'][0]
    assert estimate['currentTotal'] == '10.01'
    assert (estimate['currentAverage'], estimate['currentAverageRounded']) == ('1.43', False)
    assert estimate['currentAverageDisplay'] == '1.4'
    assert estimate['suggestedQty'] == '11'


@pytest.mark.parametrize('quantity', ['0.001', '100000', '123456789012345678901.1234567', '1e-400'])
def test_quantities_outside_the_final_limits_are_rejected_before_analysis(client, quantity):
    sid = repository.create_submission(AS_OF)
    repository.update_status(sid, 'CLASSIFYING')
    repository.update_status(sid, 'AI_UNAVAILABLE')
    response = client.post('/api/v1/confirm', json={'submissionId': str(sid), 'lines': [
        {'itemName': 'Bread', 'quantity': quantity, 'unitPrice': '1.00'}]})
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'VALIDATION_ERROR'
    assert analysis(client)['status'] == 'NO_DATA'


def test_owner_isolation_and_drafts_are_excluded(client, monkeypatch):
    history(client, logged_dates(7))
    draft = {'lines': [{'lineId': 'a', 'itemName': 'Draft item', 'quantity': '99', 'unitPrice': '1',
                        'bands': {'itemName': 'HIGH', 'quantity': 'HIGH', 'unitPrice': 'HIGH'}}]}
    sid = repository.create_submission(AS_OF)
    repository.update_status(sid, 'CLASSIFYING')
    repository.update_status(sid, 'NEEDS_VERIFICATION', draft_payload=draft)
    assert [e['itemKey'] for e in analysis(client)['estimates']] == ['bread']
    assert analysis(client)['estimates'][0]['currentTotal'] == '7'

    monkeypatch.setenv('OWNER_ID', str(uuid4()))
    assert analysis(client)['status'] == 'NO_DATA'
    provider = use_provider(MockLLMService())
    response = client.post('/api/v1/guidance', json={})
    assert response.status_code == 409 and response.json()['error']['code'] == 'INSUFFICIENT_HISTORY'
    assert provider.calls == []


def test_results_refresh_after_confirmation_and_ignore_snapshots(client):
    dates = logged_dates(7)
    history(client, dates[:6])
    assert analysis(client)['status'] == 'INSUFFICIENT_HISTORY'
    with connect() as conn:
        # A misleading snapshot for the same date must never be read back as a result.
        conn.execute("INSERT INTO baseline_estimates(id,owner_id,item_name,item_key,as_of_date,window_days,logged_days,"
                     "average_daily_quantity,trend,suggested_restock_qty) VALUES (%s,%s,'Bread','bread',%s,7,7,999,'UP',999)",
                     (uuid4(), repository.owner_id(), AS_OF))
    history(client, dates[6:])
    estimate = analysis(client)['estimates'][0]
    assert (estimate['currentAverage'], estimate['suggestedQty']) == ('1', '7')
    # The same-date confirmation changes the next result immediately.
    confirm(client, dates[-1], [('Bread', '7')])
    assert analysis(client)['estimates'][0]['suggestedQty'] == '14'
    with connect() as conn:
        # SD-03 saveBaselines: one snapshot per owner/date/item, replaced by every analysis run.
        rows = conn.execute('SELECT suggested_restock_qty, as_of_date, logged_days FROM baseline_estimates').fetchall()
    assert rows == [(14, AS_OF, 7)]
    assert repository.get_latest_baselines()[0]['suggestedRestockQty'] == 14


@pytest.mark.parametrize('method,path,kwargs', [
    ('get', '/api/v1/analysis', {}),
    ('post', '/api/v1/guidance', {'json': {}}),
])
def test_authentication_is_required_and_checked_first(client, method, path, kwargs):
    assert getattr(client, method)(path, headers={'Authorization': 'Bearer wrong'}, **kwargs).status_code == 401
    assert client.get('/api/v1/analysis?asOf=bad', headers={'Authorization': 'Bearer wrong'}).status_code == 401


def test_missing_access_code_configuration_keeps_existing_503(client, monkeypatch):
    monkeypatch.delenv('APP_ACCESS_CODE')
    assert client.get('/api/v1/analysis').status_code == 503


@pytest.mark.parametrize('value', ['bad', '2026-13-01', '2026-02-30', '20260101', '1700000000', '2026-01-01T00:00:00', '', '0001-01-01'])
def test_invalid_as_of_query(client, value):
    assert client.get('/api/v1/analysis', params={'asOf': value}).status_code == 400


@pytest.mark.parametrize('value', ['bad', '2026-13-01', 20260101, 0, True, '2026-01-01T00:00:00', '0001-01-01', '2026-05-01'])
def test_invalid_as_of_body_makes_no_provider_call(client, value):
    provider = use_provider(MockLLMService())
    assert client.post('/api/v1/guidance', json={'asOf': value}).status_code == 400
    assert provider.calls == []


def test_future_as_of_uses_one_field_error_shape(client):
    query = client.get('/api/v1/analysis', params={'asOf': '2026-05-01'})
    assert query.status_code == 400
    message = 'asOf cannot be after the current business date (2026-04-30)'
    assert query.json()['error'] == {'code': 'VALIDATION_ERROR', 'message': message, 'fields': {'asOf': message}}
    assert query.json()['detail'] == [{'loc': ['query', 'asOf'], 'type': 'value_error',
                                       'msg': 'asOf cannot be after the current business date (2026-04-30)'}]
    body = client.post('/api/v1/guidance', json={'asOf': '2026-05-01'})
    assert body.status_code == 400 and body.json()['detail'][0]['loc'] == ['body', 'asOf']
    assert analysis(client, asOf='2026-04-30')['asOf'] == '2026-04-30'  # Today itself is allowed.


def test_default_as_of_uses_the_application_clock_in_app_timezone(monkeypatch):
    from app.clock import SystemClock
    # UTC+14 and UTC-11 are 25 hours apart, so their dates always differ.
    monkeypatch.setenv('APP_TIMEZONE', 'Pacific/Kiritimati')
    ahead = SystemClock().today()
    monkeypatch.setenv('APP_TIMEZONE', 'Pacific/Pago_Pago')
    assert SystemClock().today() < ahead


def test_invalid_timezone_configuration_is_a_controlled_error(db, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv('APP_TIMEZONE', 'Not/AZone')
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get('/api/v1/analysis', headers={'Authorization': 'Bearer test-code'})
    assert response.status_code == 503


def worked_example(client):
    dates = logged_dates(14)
    history(client, dates[:7], quantity='2')
    history(client, dates[7:], quantity='4')


def test_guidance_success_through_http(client):
    worked_example(client)
    provider = use_provider(MockLLMService())
    response = client.post('/api/v1/guidance', json={'asOf': '2026-04-30'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result['source'], result['groundingPassed'], result['failedRule']) == ('AI_WORDED', True, None)
    assert result['asOf'] == '2026-04-30' and result['stockNotice'] == STOCK_NOTICE
    assert result['items'][0]['suggestedQty'] == '28' and 'plan 28 units' in result['items'][0]['message']
    assert len(provider.calls) == 1


@pytest.mark.parametrize('scenario,rule', [
    (MockScenario.UNKNOWN_ITEM, 'UNKNOWN_ITEM'), (MockScenario.CHANGED_QUANTITY, 'QUANTITY_MISMATCH'),
    (MockScenario.UNSUPPORTED_NUMBER, 'UNSUPPORTED_NUMBER'), (MockScenario.WRONG_TREND, 'TREND_MISMATCH'),
    (MockScenario.MALFORMED, 'MALFORMED_JSON'), (MockScenario.EMPTY, 'EMPTY_RESPONSE'),
    (MockScenario.FAILURE, 'PROVIDER_FAILURE'),
])
def test_grounding_failures_through_http_fall_back_to_template(client, scenario, rule):
    worked_example(client)
    provider = use_provider(MockLLMService(scenario))
    result = client.post('/api/v1/guidance', json={}).json()
    assert (result['source'], result['groundingPassed'], result['failedRule']) == ('TEMPLATE', False, rule)
    assert result['items'][0]['suggestedQty'] == '28'
    assert 'Consider having 28 on hand for your next 7 selling days' in result['items'][0]['message']
    assert result['sourceLabel'] == 'Standard advice'  # M3 9.7 template label.
    assert len(provider.calls) == 1


def test_timeout_through_http(client, monkeypatch):
    worked_example(client)
    provider = use_provider(MockLLMService(MockScenario.TIMEOUT, hang_seconds=30))
    monkeypatch.setattr(main_module, 'GuidanceEngine', lambda p: GuidanceEngine(p, timeout_seconds=0.2))
    try:
        result = client.post('/api/v1/guidance', json={}).json()
    finally:
        provider.release.set()
    assert (result['source'], result['groundingPassed'], result['failedRule']) == ('TEMPLATE', False, 'PROVIDER_TIMEOUT')
    assert result['items'][0]['message'].startswith('Bread: you sold about 4.0 per logged day')


def test_insufficient_history_makes_no_provider_call_through_http(client):
    history(client, logged_dates(6))
    provider = use_provider(MockLLMService())
    response = client.post('/api/v1/guidance', json={})
    assert provider.calls == []
    # M3 4.2: 409 INSUFFICIENT_HISTORY, with the "N of 7 so far" wording of 4.3.
    assert response.status_code == 409
    assert response.json()['error'] == {'code': 'INSUFFICIENT_HISTORY', 'fields': {},
                                        'message': 'Log at least 7 days of sales to get advice (6 of 7 so far).'}


def test_client_supplied_figures_and_scenarios_have_no_effect(client):
    worked_example(client)
    provider = use_provider(MockLLMService())
    result = client.post('/api/v1/guidance?scenario=failure', json={
        'asOf': '2026-04-30', 'suggestedQty': '1', 'items': [{'itemName': 'Bread', 'suggestedQty': '1'}], 'scenario': 'failure',
    }).json()
    assert result['source'] == 'AI_WORDED'
    assert result['items'][0]['suggestedQty'] == '28'
    assert provider.calls[0].items[0].suggestedQty == '28'


def test_guidance_uses_the_requested_as_of_for_evidence(client):
    worked_example(client)
    use_provider(MockLLMService())
    # A week earlier only the first seven logged dates (all 2s) are visible.
    result = client.post('/api/v1/guidance', json={'asOf': '2026-04-23'}).json()
    assert result['asOf'] == '2026-04-23'
    assert (result['items'][0]['suggestedQty'], result['items'][0]['trend']) == ('14', 'INSUFFICIENT_DATA')


def test_ai_provider_setting_selects_mock_or_template_without_a_key(client, monkeypatch):
    worked_example(client)
    monkeypatch.setenv('AI_PROVIDER', 'mock')
    assert client.post('/api/v1/guidance', json={}).json()['source'] == 'AI_WORDED'
    monkeypatch.setenv('AI_PROVIDER', 'some-unimplemented-provider')
    result = client.post('/api/v1/guidance', json={}).json()
    assert (result['source'], result['failedRule']) == ('TEMPLATE', 'PROVIDER_NOT_CONFIGURED')
    monkeypatch.delenv('AI_PROVIDER')
    assert client.post('/api/v1/guidance', json={}).json()['source'] == 'TEMPLATE'


def test_database_errors_keep_the_existing_safe_response(client, monkeypatch):
    provider = use_provider(MockLLMService())
    def fail(*_):
        raise psycopg.OperationalError('SECRET connection detail')
    monkeypatch.setattr(repository, 'get_confirmed_sales_between', fail)
    for response in (client.get('/api/v1/analysis'), client.post('/api/v1/guidance', json={})):
        assert response.status_code == 500
        assert 'SECRET' not in response.text
    assert provider.calls == []


def test_optional_demo_seed_is_repeatable_and_reaches_ready(client, monkeypatch):
    from app import seed_analysis
    from conftest import FixedClock
    monkeypatch.setattr(seed_analysis, 'SystemClock', lambda: FixedClock(AS_OF))
    seed_analysis.seed_analysis_history()
    seed_analysis.seed_analysis_history()
    result = analysis(client)
    assert (result['status'], result['loggedDays']) == ('READY', 14)
    estimates = {e['itemKey']: e for e in result['estimates']}
    assert estimates['bread']['trend'] == 'UP' and estimates['bread']['suggestedQty'] == '28'
    assert estimates['milk']['trend'] == 'STABLE' and estimates['eggs']['trend'] == 'DOWN'
    assert Decimal(estimates['milk']['currentAverage']) == Decimal('2.5')
    assert date.fromisoformat(result['loggedDates'][-1]) == AS_OF - timedelta(days=1)

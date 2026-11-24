import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import SPEC
from app.errors import SurveyProblem
from app.modules.ledger.identity import canonical, sha
from app.modules.ledger.service import archive
from app.modules.ledger.storage import Archive
from app.modules.release.service import release
from app.modules.release.withdraw import withdraw
from app.modules.returns.scrub import guarded, scrubbed_returns
from app.source import sources


@pytest.fixture
def store(tmp_path):
    return Archive(tmp_path/'withdraw.db')


def test_scrub_keeps_shape(tables):
    text, removed = scrubbed_returns(tables['returns'], {'00011'})
    assert removed == 3 and text.startswith('﻿')
    lines = text.splitlines()
    headers = [line.lstrip('﻿') for line in lines].count('ticket,trip,reported_layer,visit,status,probability,choice')
    assert lines[0].lstrip('﻿').startswith('ticket') and headers == 2
    assert '00011' not in text and '00099,0001,枢纽,1,YES,1/2,other,多列' in text
    assert text.endswith('\n\n')


def test_scrub_bad_width_row(tables):
    text, removed = scrubbed_returns(tables['returns'], {'00099'})
    assert removed == 1 and '多列' not in text


def test_guard_without_tickets_keeps_text(tables):
    assert guarded(tables, set()) is tables
    assert guarded(tables, {'00999'}) == tables


def test_erasure(store, tables):
    key = archive(store, tables)['id']
    assert withdraw(store, '00011')['runs'] == [key]
    manifest, kept, results = store.read(key)
    assert '00011' not in kept['returns']
    assert '00011' not in json.dumps(results, ensure_ascii=False)
    assert results['returns']['totals']['input'] == 15
    assert results['frame']['counts'] == {'valid': 5, 'invalid': 4, 'nonresponse': 2}


def test_recalculated_counts(store, tables):
    key = archive(store, tables)['id']
    withdraw(store, '00011')
    _, _, results = store.read(key)
    balance = results['balance']
    assert (balance['population'], balance['estimated'], balance['unknown']) == (290, 250, 40)
    assert results['allocation']['totals'] == {'direct': 134, 'transfer': 33, 'other': 83}
    assert balance['balanced']


def test_legacy_seal_verified(store, tables):
    key = archive(store, tables)['id']
    before = release(*store.read(key))
    withdraw(store, '00011')
    proof = release(*store.read(key), receipts=store.receipts(key))
    assert proof['verified'] and proof['legacy_seal'] == before['seal']
    assert proof['seal'] != before['seal']
    assert [row['ticket'] for row in proof['withdrawals']] == ['00011']
    assert proof['withdrawals'][0]['removed'] == 3


def test_chain_across_two_withdrawals(store, tables):
    key = archive(store, tables)['id']
    first = release(*store.read(key))['seal']
    withdraw(store, '00011')
    withdraw(store, '00014')
    manifest, kept, results = store.read(key)
    assert '00014' not in kept['returns'] and '00014' not in json.dumps(results, ensure_ascii=False)
    proof = release(manifest, kept, results, store.receipts(key))
    assert proof['verified'] and proof['legacy_seal'] == first
    assert [row['ticket'] for row in proof['withdrawals']] == ['00011', '00014']
    assert results['frame']['counts']['nonresponse'] == 1


def test_tampered_scrubbed_content_rejected(store, tables):
    key = archive(store, tables)['id']
    withdraw(store, '00011')
    manifest, kept, results = store.read(key)
    kept['returns'] += '00011,0001,城北,3,YES,1/2,other\n'
    with pytest.raises(SurveyProblem) as error:
        release(manifest, kept, results, store.receipts(key))
    assert error.value.code == 'BAD_SEAL'


def test_retry_adds_no_issuance(store, tables):
    key = archive(store, tables)['id']
    first = withdraw(store, '00011')
    receipts = store.receipts(key)
    second = withdraw(store, '00011')
    assert first['reused'] is False and second['reused'] is True
    assert second['runs'] == first['runs'] == [key]
    assert store.receipts(key) == receipts and store.withdrawn() == {'00011'}


def test_unknown_ticket_withdrawal(store, tables):
    archive(store, tables)
    out = withdraw(store, '00456')
    assert out['runs'] == [] and store.withdrawn() == {'00456'}


def test_late_visit_not_resampled(store, tables):
    archive(store, tables)
    withdraw(store, '00011')
    late = dict(tables)
    late['returns'] += '00011,0001,枢纽,3,YES,1/2,direct\n'
    out = archive(store, late)
    _, kept, results = store.read(out['id'])
    assert '00011' not in kept['returns']
    assert '00011' not in json.dumps(results, ensure_ascii=False)
    assert results['frame']['counts']['valid'] == 5


def test_concurrent_same_withdrawal(store, tables):
    archive(store, tables)
    with ThreadPoolExecutor(max_workers=3) as pool:
        result = list(pool.map(lambda _: withdraw(store, '00011'), range(3)))
    assert sum(not item['reused'] for item in result) == 1
    assert store.withdrawn() == {'00011'}


def test_run_id_binds_pre_withdrawal_content(store, tables):
    key = archive(store, tables)['id']
    withdraw(store, '00011')
    receipt = store.receipts(key)[0]
    assert receipt['pre_files'] == store.read(key)[0]['files']
    assert sha(canonical({'files': receipt['pre_files'], 'version': SPEC})) == key


def test_withdrawal_spans_all_archives(store, tables):
    first = archive(store, tables)['id']
    second = archive(store, sources({'sample': 'clean'}))['id']
    out = withdraw(store, '00011')
    assert sorted(out['runs']) == sorted([first, second])
    for key in (first, second):
        manifest, kept, results = store.read(key)
        assert '00011' not in kept['returns']
        assert '00011' not in json.dumps(results, ensure_ascii=False)
        assert release(manifest, kept, results, store.receipts(key))['verified']


def test_api_withdrawal_flow(client):
    key = client.post('/api/runs', json={'sample': 'dirty'}).json['id']
    sealed = client.get('/api/export/' + key).json['seal']
    answer = client.post('/api/withdrawals', json={'ticket': '00011'})
    assert answer.status_code == 201 and answer.json['runs'] == [key]
    retry = client.post('/api/withdrawals', json={'ticket': '00011'})
    assert retry.status_code == 200 and retry.json['reused']
    assert client.get('/api/withdrawals').json['tickets'] == ['00011']
    proof = client.get('/api/export/' + key).json
    assert proof['verified'] and proof['legacy_seal'] == sealed
    assert (proof['results']['balance']['estimated'], proof['results']['balance']['unknown']) == (250, 40)
    rerun = client.post(f'/api/runs/{key}/rerun')
    assert rerun.status_code == 200 and rerun.json['reused']
    assert rerun.json['balance']['estimated'] == 250


def test_api_withdrawal_requires_ticket(client):
    response = client.post('/api/withdrawals', json={})
    assert (response.status_code, response.json['code']) == (422, 'BAD_TICKET')


def test_preview_excludes_withdrawn(client):
    client.post('/api/withdrawals', json={'ticket': '00011'})
    page = client.get('/survey/returns').get_data(as_text=True)
    assert '00011' not in page
    api = client.post('/api/survey/returns', json={'sample': 'dirty'}).json
    assert '00011' not in json.dumps(api, ensure_ascii=False)


def test_release_page_shows_legacy_seal(client):
    key = client.post('/api/runs', json={'sample': 'dirty'}).json['id']
    client.post('/api/withdrawals', json={'ticket': '00011'})
    page = client.get(f'/survey/release?run={key}').get_data(as_text=True)
    assert '撤回前签发摘要' in page and '剔除后重算人数' in page and '00011' in page

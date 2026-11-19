from importlib import import_module
from io import BytesIO
import json
from sqlite3 import OperationalError
from urllib.error import HTTPError
from uuid import uuid4

import pytest

from backend.agent.investment_review import prepare_review, run_review
from backend.agent.limits import ResearchLimits
from backend.agent.models import InvestmentAnalysis
from backend.providers.live import NebiusModelProvider


def make_job():
    draft = InvestmentAnalysis(stance='bullish', confidence='medium', narrative='Original NVIDIA draft text.',
        missing_inputs=[], points=[
            {'topic': 'operating_state', 'text': 'Operating discussion', 'evidence_ids': ['official']},
            {'topic': 'thesis', 'text': 'Conditional investment thesis', 'evidence_ids': ['official', 'web']},
            {'topic': 'supporting_case', 'text': 'Supporting case', 'evidence_ids': ['web']},
            {'topic': 'risk', 'text': 'Risk discussion', 'evidence_ids': ['official']},
        ])
    return prepare_review(draft, {'question':'NVIDIA',
                                  'verified_financial_facts': [{'company_id':'nvda','evidence_ids':['official']}],
                                  'unverified_external_leads':[{'source_id':'web'}]},
                          {'nvda'}, {str(i): point.evidence_ids for i, point in enumerate(draft.points)},
                          'zai-org/GLM-5.3-Flash')


def limits():
    result = ResearchLimits()
    result.providers['nebius'].enabled = True
    return result


def response(request, *, content=None, finish='stop', tokens=100):
    body = json.loads(request.data)
    payload = json.loads(body['messages'][1]['content'])
    if content is None:
        content = json.dumps({'verdicts':[{'item_id': point['item_id'], 'verdict':'supported',
                                          'short_reason':'Cited evidence supports the inference.'}
                                         for point in payload['items']]})
    return BytesIO(json.dumps({'choices':[{'finish_reason':finish, 'message':{'content':content}}],
                                'usage':{'prompt_tokens':300, 'completion_tokens':tokens}}).encode())


def test_truncated_batch_splits_preserves_draft_and_other_batches():
    seen = []
    def opener(request, timeout):
        body = json.loads(request.data)
        batch = json.loads(body['messages'][1]['content'])['batch_id']
        seen.append(batch)
        if batch == 'thesis-1':
            return response(request, content='{"verdicts":[', finish='length', tokens=body['max_tokens'])
        return response(request)
    job = make_job()
    model = NebiusModelProvider(api_key='fixture', opener=opener)
    result, state = run_review(job, model, limits(), lambda: None)
    assert seen == ['operations-1', 'thesis-1', 'thesis-1.1', 'thesis-1.2', 'risks-1']
    assert state.opinion == 'approved' and state.review == 'review_completed'
    assert job['draft']['narrative'] == 'Original NVIDIA draft text.'
    assert result.narrative is None
    failed = job['attempt_log'][1]
    assert failed['failure_kind'] == 'truncated' and failed['event']['finish_reason'] == 'length'
    assert failed['response'] == '{"verdicts":['


@pytest.mark.parametrize('fault', [429, 500, 529, 401, 'timeout', 'json', 'exhausted'])
def test_retry_classifies_fault_and_keeps_earlier_results(monkeypatch, fault):
    monkeypatch.setattr('backend.agent.investment_review.time.sleep', lambda _: None)
    seen = {}
    def opener(request, timeout):
        body = json.loads(request.data)
        batch = json.loads(body['messages'][1]['content'])['batch_id']
        seen[batch] = seen.get(batch,0) + 1
        if batch == 'thesis-1' and (seen[batch] == 1 or fault == 'exhausted'):
            if fault == 'json':
                return response(request, content='not json')
            if fault in ('timeout', 'exhausted'):
                raise TimeoutError
            raise HTTPError(request.full_url, fault, '', {'Retry-After':'2'}, None)
        return response(request)
    job = make_job()
    result, state = run_review(job, NebiusModelProvider(api_key='fixture', opener=opener), limits(), lambda: None)
    expected = 1 if fault == 401 else 3 if fault == 'exhausted' else 2
    assert seen['operations-1'] == 1 and seen['thesis-1'] == expected
    assert job['draft']['narrative'] == 'Original NVIDIA draft text.'
    if fault in (401, 'exhausted'):
        assert state.opinion == 'provisional' and state.review == 'review_partial'
        assert any(point.topic == 'operating_state' for point in result.points)
        assert result.stance not in {'neutral', 'provisional_neutral'}
    else:
        assert state.opinion == 'approved'


def test_persist_original_and_restart_without_replaying_inflight_attempt(monkeypatch, tmp_path):
    store = import_module('backend.storage.store')
    monkeypatch.setenv('SELDON_DATA_DIR', str(tmp_path))
    task_id = str(uuid4())
    job = make_job()
    report = {'task_id':task_id}
    def checkpoint():
        store.save_review_checkpoint(task_id, report, job)
    checkpoint()
    seen = []
    def crash_after_first(request, timeout):
        batch = json.loads(json.loads(request.data)['messages'][1]['content'])['batch_id']
        seen.append(batch)
        if batch == 'thesis-1':
            raise SystemExit
        return response(request)
    with pytest.raises(SystemExit):
        run_review(job, NebiusModelProvider(api_key='fixture', opener=crash_after_first), limits(), checkpoint)
    _, recovered_report, job = store.load_pending_reviews()[0]
    assert recovered_report == report and job['draft']['narrative'] == 'Original NVIDIA draft text.'
    _, state = run_review(job, NebiusModelProvider(api_key='fixture', opener=crash_after_first), limits(), checkpoint)
    assert seen == ['operations-1', 'thesis-1', 'risks-1']
    assert state.batch_status['thesis-1'] == 'interrupted'
    assert state.opinion == 'provisional' and state.verdicts['0'] == 'supported'
    _, repeated = run_review(job, NebiusModelProvider(api_key='fixture', opener=crash_after_first), limits(), checkpoint)
    assert repeated == state and len(seen) == 3


def test_db_failure_prevents_review_dispatch_and_keeps_provisional_draft(monkeypatch):
    api = import_module('backend.app')
    def fail(*args):
        raise OperationalError('fixture')
    monkeypatch.setattr(api, 'save_review_checkpoint', fail)
    report = {'limitations':[], 'providers':{}}
    result = api._review_report(str(uuid4()), report, make_job(), limits())
    assert result['investment_draft']['narrative'] == 'Original NVIDIA draft text.'
    assert result['investment_review']['review'] == 'review_blocked'
    assert result['investment_review']['opinion'] == 'provisional'
    assert not result['provider_events']


def test_deadline_leaves_unsent_batches_for_recovery(monkeypatch):
    job = make_job()
    expired = limits()
    expired.request_timeout_seconds = 0
    model = NebiusModelProvider(api_key='fixture', opener=lambda request, timeout: response(request))
    _, pending = run_review(job, model, expired, lambda: None)
    assert pending.opinion == 'provisional'
    assert set(pending.batch_status.values()) == {'pending'}
    assert job['reserved'] == 0 and not job['attempt_log']
    _, recovered = run_review(job, model, limits(), lambda: None)
    assert recovered.opinion == 'approved'


def test_nvidia_legacy_output_limit_with_no_review_replay_of_research(monkeypatch):
    # 2,978 synthesis tokens and 1,024 review tokens reproduce the observed
    # sizes. The original provider response was not saved.
    monkeypatch.setenv('INVESTMENT_REVIEW_BATCH_TOKENS', '1024')
    job = make_job()
    session = limits()
    session.record_tokens(6597, 2978)
    seen = []
    def opener(request, timeout):
        payload = json.loads(json.loads(request.data)['messages'][1]['content'])
        seen.append(payload['batch_id'])
        if payload['batch_id'] == 'thesis-1':
            return response(request, content='{"verdicts":[', finish='length', tokens=1024)
        return response(request)
    _, summary = run_review(job, NebiusModelProvider(api_key='fixture', opener=opener), session, lambda: None)
    assert summary.opinion == 'approved'
    assert job['attempt_log'][1]['event']['completion_tokens'] == 1024
    assert job['draft']['narrative'] == 'Original NVIDIA draft text.'
    assert seen.count('operations-1') == 1
    assert all(event.stage == 'investment_review' for event in session.events)
    assert session.rounds_used == 0 and session.queries_used == 0


def test_stale_checkpoint_cannot_overwrite_a_newer_attempt(monkeypatch, tmp_path):
    from copy import deepcopy
    store = import_module('backend.storage.store')
    monkeypatch.setenv('SELDON_DATA_DIR', str(tmp_path))
    task_id = str(uuid4())
    job = make_job()
    store.save_review_checkpoint(task_id, {}, job)
    stale = deepcopy(job)
    store.save_review_checkpoint(task_id, {}, job)
    with pytest.raises(store.ReviewCheckpointConflict):
        store.save_review_checkpoint(task_id, {}, stale)
    assert store.load_pending_reviews()[0][2]['_revision'] == 2


def test_application_persists_draft_before_first_review_dispatch(monkeypatch, tmp_path):
    from backend.agent.models import Report

    api = import_module('backend.app')
    store = import_module('backend.storage.store')
    monkeypatch.setenv('SELDON_DATA_DIR', str(tmp_path))
    task_id, report_id = str(uuid4()), str(uuid4())
    monkeypatch.setattr(api, '_tasks', {task_id: {'task_id': task_id}})
    monkeypatch.setattr(api, '_reports', {})
    monkeypatch.setattr(api, '_live_enabled', True)
    job = make_job()

    def run_agent(*args, **kwargs):
        kwargs['limits'].providers['nebius'].enabled = True
        kwargs['on_draft'](job)
        return Report(report_id=report_id, task_id='', query='NVIDIA', generated_at=job['generated_at'],
                      data_mode='live', status='completed', companies=[], metrics=[], claims=[], sources=[],
                      limitations=[], providers=kwargs['limits'].provider_snapshot())

    def opener(request, timeout):
        stored_id, stored_report, saved = store.load_pending_reviews()[0]
        assert stored_id == task_id and stored_report['report_id'] == report_id
        assert saved['draft']['narrative'] == 'Original NVIDIA draft text.'
        assert saved['in_flight']
        return response(request)

    from types import SimpleNamespace
    monkeypatch.setattr(api, '_orchestrator', SimpleNamespace(run_agent=run_agent))
    monkeypatch.setattr(api, '_model_provider', NebiusModelProvider(api_key='fixture', opener=opener))
    api._run(task_id, api.ResearchRequest(query='NVIDIA investment'))
    saved = api._reports[report_id]
    assert saved['investment_review']['opinion'] == 'approved'
    assert saved['investment_draft']['narrative'] == 'Original NVIDIA draft text.'
    assert saved['investment_analysis']['narrative'] is None
    assert not store.load_pending_reviews()


def test_partial_review_recovers_complete_verdicts_from_truncated_response():
    from backend.agent.investment_review import _decode

    body = '{"verdicts":[{"item_id":"1","verdict":"supported"},{"item_id":"2","verdict":"unfini'
    assert _decode(body, ['1', '2'], partial=True) == {'1': ('supported', '')}


def test_comparative_answer_survives_without_single_stock_stance():
    from backend.agent.investment_review import summarize

    job = make_job()
    job['draft']['stance'] = 'insufficient'
    job['draft']['points'][1]['text'] = 'I favor Apple for a stability-first investor.'
    job['context']['saved_report_context'] = [{'report_id': 'archived-report'}]
    job['queue'] = []
    job['batch_status'] = {'thesis-1': 'completed'}
    job['verdicts']['1'] = 'supported'
    analysis, _ = summarize(job)
    assert analysis.points[0].text == 'I favor Apple for a stability-first investor.'

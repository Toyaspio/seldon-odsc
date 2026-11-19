from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.agent.investment import analyze_investment
from backend.agent.limits import ResearchLimits
from backend.agent.models import InvestmentAnalysis
from backend.providers.live import NebiusModelProvider, ProviderError


@pytest.mark.parametrize('mode,expected', [
    ('grounded', 'bullish'), ('factual_question', 'bullish'), ('unknown_reference', 'insufficient'),
    ('mixed_references', 'bullish'),
    ('no_external_evidence', 'bullish'), ('historical', 'bullish'),
    ('provider_failed', 'insufficient'), ('unrelated', 'insufficient'),
    ('missing_issuer', 'insufficient'), ('review_unavailable', 'insufficient'),
])
def test_investment_evidence_boundary(monkeypatch, mode, expected):
    model = NebiusModelProvider()
    ids = {
        'unknown_reference': ['fabricated'],
        'mixed_references': ['official', 'fabricated'],
    }.get(mode, ['official', 'lead'])
    if mode in {'no_external_evidence', 'historical'}:
        ids = ['official']
    call = Mock(return_value=InvestmentAnalysis.model_validate({
        'stance': 'bullish', 'confidence': 'medium',
        'points': [{'topic': 'thesis', 'text': 'Conditional upside', 'evidence_ids': ids}],
        'missing_inputs': [],
    }))
    if mode == 'provider_failed':
        call.side_effect = ProviderError('private')
    monkeypatch.setattr(NebiusModelProvider, 'synthesize_investment', call)
    audit = Mock(return_value=set() if mode == 'unrelated' else {0})
    if mode == 'review_unavailable':
        audit.side_effect = ProviderError('private')
    monkeypatch.setattr(NebiusModelProvider, 'review_investment', audit)
    fact = {'company_id': 'issuer', 'period_end': '2026-01-01', 'metric_name': 'revenue',
            'actual_or_guidance': 'actual', 'evidence_ids': ['official'], 'duration_kind': 'quarter',
            'currency': 'USD', 'unit': 'USD', 'accounting_basis': 'GAAP', 'capex_scope': None}
    metric = SimpleNamespace(**fact, model_dump=lambda **kwargs: fact)
    plan = SimpleNamespace(core_question='What is the upside?', verification_questions=['Why?'])
    sources = [SimpleNamespace(source_id='official')]
    leads = [SimpleNamespace(source_id='lead', publication_date=None, publisher='Source',
                             company_id='issuer', title='Issuer outlook', quoted_text='Reported expectations')]
    question = 'What is the latest quarterly revenue?' if mode == 'factual_question' else 'Investment outlook'
    result = analyze_investment(question, plan, [metric], sources,
                                [] if mode == 'no_external_evidence' else leads, model,
                                ResearchLimits(), Mock(), [], mode == 'historical',
                                issuer_ids=('issuer', 'uncited') if mode == 'missing_issuer' else ())
    assert result.stance == expected
    assert bool(result.points) == (expected == 'bullish')
    if mode == 'mixed_references':
        assert result.points[0].evidence_ids == ['official']
    if mode == 'unknown_reference':
        assert not audit.called
    assert call.called


def test_comparative_research_queues_new_task_without_editing_reports(monkeypatch):
    from copy import deepcopy
    from importlib import import_module

    from fastapi.testclient import TestClient

    api = import_module('backend.app')
    original = {
        report_id: {
            'report_id': report_id, 'task_id': str(index), 'query': 'Original report',
            'generated_at': '2026-01-01T00:00:00Z', 'data_mode': 'live', 'status': 'partial',
            'companies': [{'company_id': report_id, 'company_name': name, 'cik': cik,
                           'resolution_status': 'resolved'}],
            'claims': [], 'metrics': [], 'sources': [], 'limitations': [],
        }
        for index, (report_id, name, cik) in enumerate([
            ('msft', 'Microsoft', '0000789019'), ('nvda', 'NVIDIA', '0001045810'),
        ])
    }
    backup = deepcopy(original)
    monkeypatch.setattr(api, '_reports', original)
    monkeypatch.setattr(api, '_tasks', {})
    monkeypatch.setattr(api, '_live_enabled', True)
    monkeypatch.setattr(api, '_model_mode', 'mock')
    monkeypatch.setattr(api, '_search_mode', 'tavily')
    monkeypatch.setattr(api, '_financial_mode', 'sec')
    monkeypatch.setattr(api, 'save_task', Mock())
    worker = Mock()
    monkeypatch.setattr(api, '_executor', worker)
    client = TestClient(api.app)
    for ids in (['msft'], ['msft', 'msft'], ['msft', 'missing']):
        assert client.post('/api/research', json={'query': 'Compare growth', 'report_ids': ids}).status_code == 422
    payload = {'query': 'Compare growth', 'report_ids': ['msft', 'nvda']}
    assert client.post('/api/research', json=payload).status_code == 409
    monkeypatch.setattr(api, '_model_mode', 'nebius')
    monkeypatch.setattr(api, 'runtime_values', lambda *keys: ('fixture', None))
    assert client.post('/api/research', json=payload).status_code == 409
    monkeypatch.setattr(api, 'runtime_values', lambda *keys: ('fixture', 'fixture'))
    result = client.post('/api/research', json=payload)
    assert result.status_code == 202
    function, task_id, request, inputs = worker.submit.call_args.args
    assert function is api._run
    assert task_id == result.json()['task_id']
    assert request.companies == ['0000789019', '0001045810']
    assert [report.report_id for report in inputs] == ['msft', 'nvda']
    assert original == backup



def test_saved_report_keeps_valid_rows_when_one_record_is_damaged():
    from backend.app import _read_saved_report

    original = {'report_id': 'archived', 'task_id': 'task', 'query': 'Compare companies',
                'generated_at': '2026-01-01T00:00:00Z', 'data_mode': 'mixed',
                'status': 'partial', 'metrics': [], 'claims': [], 'sources': [], 'limitations': [],
                'companies': [None, {'company_id': 'para', 'company_name': 'Parabolic',
                               'ticker': 'PARA', 'resolution_status': 'resolved'}]}
    recovered = _read_saved_report(original)
    assert [company.company_id for company in recovered.companies] == ['para']
    assert original['companies'][0] is None

def test_financial_context_retains_period_and_asset_scope(monkeypatch):
    from backend.agent.models import Metric

    template = dict(company_id='issuer', metric_name='cash_capex', value=5, currency='USD',
                    unit='USD', period_start='2025-10-01', period_end='2025-12-31',
                    fiscal_period='Q4', accounting_basis='GAAP', actual_or_guidance='actual')
    metrics = [
        Metric(**(template | changes), metric_id=key, evidence_ids=[key])
        for key, changes in [
            ('ppe', {'capex_scope': 'ppe', 'duration_kind': 'quarter'}),
            ('broad', {'capex_scope': 'productive_assets', 'duration_kind': 'quarter'}),
            ('ytd', {'capex_scope': 'ppe', 'duration_kind': 'ytd', 'period_start': '2025-04-01'}),
        ]
    ]
    model = NebiusModelProvider()
    call = Mock(return_value=InvestmentAnalysis(stance='insufficient', confidence='low',
                                               points=[], missing_inputs=[]))
    monkeypatch.setattr(NebiusModelProvider, 'synthesize_investment', call)
    plan = SimpleNamespace(core_question='Is there upside?', verification_questions=[])
    leads = [SimpleNamespace(source_id='lead', publication_date=None, publisher='Issuer',
                             title='Outlook', quoted_text='Expectations', company_id='issuer')]
    analyze_investment('Investment outlook', plan, metrics,
                       [SimpleNamespace(source_id=m.metric_id) for m in metrics],
                       leads, model, ResearchLimits(), Mock(), [], False)
    assert {m['metric_id'] for m in call.call_args.args[0]['verified_financial_facts']} == {'ppe', 'broad', 'ytd'}


def test_tavily_operation_without_http_dispatch(monkeypatch):
    from backend.agent.pipeline import ToolRouter
    from backend.providers.live import TavilySearchProvider

    monkeypatch.setattr('backend.providers.live.runtime_values', lambda *keys: (None,))
    limits = ResearchLimits()
    limits.providers['tavily'].enabled = True
    opener = Mock()
    router = ToolRouter(TavilySearchProvider(opener=opener), None)
    assert router.search_round(['Example'], limits, []) == []
    assert not opener.called
    activity = limits.providers['tavily']
    assert (activity.calls, activity.succeeded, activity.failed) == (1, 0, 1)
    assert (activity.http_attempts, activity.http_responses) == (0, 0)


@pytest.mark.parametrize('recovery_available', [True, False])
def test_finished_report_survives_database_failure(monkeypatch, tmp_path, recovery_available):
    from importlib import import_module
    from sqlite3 import OperationalError
    from uuid import uuid4

    api = import_module('backend.app')
    store = import_module('backend.storage.store')
    monkeypatch.setenv('SELDON_DATA_DIR', str(tmp_path))
    task_id = str(uuid4())
    monkeypatch.setattr(api, '_tasks', {task_id: {'task_id': task_id}})
    monkeypatch.setattr(api, '_reports', {})
    monkeypatch.setattr(api, '_live_enabled', False)
    monkeypatch.setattr(api, 'save_task', Mock())
    monkeypatch.setattr(api, 'save_completion', Mock(side_effect=OperationalError('locked')))
    if not recovery_available:
        monkeypatch.setattr(api, 'stage_completion', Mock(side_effect=OSError('full')))
    api._run(task_id, api.ResearchRequest(query='Research Microsoft', companies=['MSFT']))
    task = api._tasks[task_id]
    assert task['status'] in {'completed', 'partial'}
    assert task['progress'] == 100
    assert task['report_id'] in api._reports
    warning = api._reports[task['report_id']]['limitations'][0]
    assert ('recovery is pending' if recovery_available else 'only until the service stops') in warning
    if recovery_available:
        tasks, reports = store.load_all()
        assert tasks[task_id]['report_id'] == task['report_id']
        assert reports[task['report_id']]['task_id'] == task_id
        assert not list((tmp_path / 'pending-completions').glob('*.json'))


def test_tavily_http_retry_counts_responses_separately(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError

    from backend.agent.pipeline import ToolRouter
    from backend.providers.live import TavilySearchProvider

    monkeypatch.setattr('backend.providers.live.time.sleep', lambda _: None)
    opener = Mock(side_effect=[HTTPError('https://api.tavily.com/search', 503, '', {}, None),
                               BytesIO(b'{"results": []}')])
    limits = ResearchLimits()
    limits.providers['tavily'].enabled = True
    assert ToolRouter(TavilySearchProvider(api_key='fixture', opener=opener), None).search_round(
        ['Example'], limits, []) == []
    activity = limits.providers['tavily']
    assert (activity.calls, activity.succeeded, activity.http_attempts, activity.http_responses) == (1, 1, 2, 2)


@pytest.mark.parametrize('query,expected', [
    ('Has Nvidia released its latest earnings report, and what is the impact?', True),
    ('How do the quarterly results affect the outlook?', True),
    ('英伟达最新财报发布了吗，对公司未来经营有什么影响？', True),
    ('What are the revenue and cash flow from the latest earnings report?', False),
    ('英伟达最新财报发布了吗？', False),
])
def test_investment_intent_in_earnings_questions(query, expected):
    from backend.agent.investment import investment_requested

    assert investment_requested(query) is expected

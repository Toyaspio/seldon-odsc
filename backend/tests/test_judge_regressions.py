"""Offline regressions for the approved judge-facing fixes."""
from copy import deepcopy
from importlib import import_module
import time

import pytest
from fastapi.testclient import TestClient

from backend.agent.investment_review import summarize, investment_article
from backend.agent.models import InvestmentAnalysis, Company, Metric
from backend.agent.pipeline import CompanyResolver, evidence_gaps, scoped_research_quality
from backend.agent.planning import ResearchPlan
from backend.agent.limits import ResearchLimits
from backend.providers.sec import SecFinancialDataProvider
from test_investment_review import make_job
from test_phase2 import pipeline, Planner, PLAN, Facts, fact


@pytest.mark.parametrize('stance', ['bullish', 'cautiously_bullish', 'bearish', 'neutral'])
@pytest.mark.parametrize('verdict', ['partially_supported', 'needs_qualification'])
def test_qualified_thesis_keeps_direction_and_complete_text(stance, verdict):
    job = make_job()
    job['draft']['stance'] = stance
    job['draft']['points'][1]['text'] = 'x' * 900
    job['verdicts'] = {'0': 'supported', '1': verdict, '3': 'contradicted'}
    job['reasons'] = {'1': 'The direction holds only while cash collections remain positive.'}
    job['context']['article_language'] = 'en'
    analysis, state = summarize(job)
    assert analysis.stance == stance and state.opinion == 'provisional'
    assert analysis.points[0].text == 'x' * 900
    InvestmentAnalysis.model_validate(analysis.model_dump())
    section = investment_article(job)['sections'][1]
    assert section['review_reason'] == job['reasons']['1']
    assert section['evidence_ids'] == ['official', 'web']
    assert not any(point.topic == 'risk' for point in analysis.points)


@pytest.mark.parametrize('verdict', ['contradicted', 'unsupported'])
def test_primary_counterevidence_still_prevents_direction(verdict):
    job = make_job()
    job['verdicts'] = {'0': 'supported', '1': verdict}
    analysis, _ = summarize(job)
    assert analysis.stance == 'insufficient'
    assert all(point.topic != 'thesis' for point in analysis.points)


def test_archived_issuer_facts_can_support_comparison():
    job = make_job()
    facts = job['context']['verified_financial_facts']
    job['context']['verified_financial_facts'] = []
    job['context']['saved_report_context'] = [{'generated_at': '2026-11-19', 'verified_claims': facts}]
    job['verdicts'] = {'0': 'supported', '1': 'supported'}
    analysis, state = summarize(job)
    assert analysis.stance == 'bullish' and state.opinion == 'approved'


def test_reopening_qualified_old_report_uses_saved_review_without_research(monkeypatch):
    api = import_module('backend.app')
    job = make_job()
    job['context']['article_language'] = 'en'
    job['verdicts'] = {'1': 'needs_qualification'}
    job['reasons'] = {'1': 'Assumes continued cash collections.'}
    report = {'task_id': 'old', 'status': 'partial', 'research_quality': {},
              'investment_narrative': investment_article(job)}
    original = deepcopy(report)
    monkeypatch.setattr(api, 'load_review_for_report', lambda task: ({'status': 'completed'}, job))
    restored = api._restore_saved_investment_view(report)
    assert restored['investment_analysis']['stance'] == 'bullish'
    assert restored['investment_review']['opinion'] == 'provisional'
    assert restored['status'] == 'partial' and report == original


def test_imported_qualified_report_can_reopen_without_private_checkpoint(monkeypatch):
    api = import_module('backend.app')
    job = make_job()
    job['context']['article_language'] = 'en'
    job['verdicts'] = {'1': 'needs_qualification'}
    job['reasons'] = {'1': 'Assumes continued cash collections.'}
    _, review = summarize(job)
    report = dict(task_id='imported', status='partial', research_quality={},
                  investment_draft=job['draft'], investment_review=review.model_dump(mode='json'),
                  investment_narrative=investment_article(job), metrics=[], companies=[])
    monkeypatch.setattr(api, 'load_review_for_report', lambda task: None)
    restored = api._restore_saved_investment_view(report)
    assert restored['investment_analysis']['stance'] == 'bullish'
    assert restored['investment_review']['opinion'] == 'provisional'


@pytest.mark.parametrize('query,tickers', [
    ('Compare AWS and Google Cloud', {'AMZN', 'GOOGL'}),
    ('Google and NVDA', {'GOOGL', 'NVDA'}),
    ('比较谷歌与英伟达', {'GOOGL', 'NVDA'}),
    ('drawstring comparison', set()),
])
def test_brand_aliases_and_word_boundaries(query, tickers):
    resolver = CompanyResolver()
    assert {company.ticker for company in resolver.resolve(resolver.mentioned(query))} == tickers


@pytest.mark.parametrize('official_second', [True, False])
def test_search_recovers_second_issuer_without_losing_first(monkeypatch, official_second):
    agent = pipeline()
    provider = SecFinancialDataProvider()
    agent.router.financial = provider
    directory = [dict(name='Microsoft Corporation', ticker='MSFT', cik='0000789019'),
                 dict(name='MEDIROM Healthcare Technologies Inc.', ticker='MRM', cik='0001819704')]
    monkeypatch.setattr(Planner, 'plan', lambda self, query: {**PLAN, 'companies': ['MSFT', 'EDIROM Healthcare Technologies']})
    monkeypatch.setattr(provider, 'directory', lambda timeout: directory)
    monkeypatch.setattr(provider, 'issuer_labels', lambda cik, timeout: [row['name'] for row in directory if row['cik'] == cik])
    monkeypatch.setattr(provider, 'facts', Facts().facts)
    monkeypatch.setattr(agent.router.search, 'search', lambda *_: [
        {'url': 'https://www.sec.gov/Archives/edgar/data/789019/fixture/msft.htm'},
        {'url': 'https://www.sec.gov/Archives/edgar/data/' + ('1819704' if official_second else '9999999') + '/fixture/mrm.htm'}])
    report = agent.run_agent('Compare Microsoft with EDIROM Healthcare Technologies', [])
    assert {company.ticker for company in report.companies} == ({'MSFT', 'MRM'} if official_second else {'MSFT', None})
    assert {row.company_id for row in report.metrics} == ({'msft', 'mrm'} if official_second else {'msft'})


def quarter():
    return Metric(metric_id='quarter', company_id='nvda', metric_name='revenue', value=100,
                  currency='USD', unit='USD', period_start='2026-07-01', period_end='2026-09-30',
                  fiscal_period='Q3', accounting_basis='US GAAP', actual_or_guidance='actual', evidence_ids=['s'])


@pytest.mark.parametrize('query,missing', [
    ('What was NVIDIA full year revenue?', True), ('英伟达全年营收多少', True),
    ('What is NVIDIA quarterly revenue?', False), ('What is NVIDIA revenue year over year?', False),
    ('NVIDIA annual revenue growth', False), ('What is NVIDIA revenue?', False),
    ('NVIDIA YTD revenue', True), ('NVIDIA trailing 12 months revenue', False),
])
def test_explicit_period_gap_does_not_discard_other_periods(query, missing):
    company = Company(company_id='nvda', company_name='NVIDIA', resolution_status='resolved')
    plan = ResearchPlan(companies=['NVDA'], core_question=query, metrics=['revenue'],
                        retrieval_tasks=[{'query': query}], comparison='latest')
    rows = [quarter()]
    gaps = evidence_gaps([company], plan, rows, query)
    quality = scoped_research_quality(query, [company], rows, [], gaps, [])
    assert bool(quality.core_gaps) is missing
    assert rows[0].value == 100 and rows[0].fiscal_period == 'Q3'


@pytest.mark.parametrize('setting', ['MAX_MODEL_CALLS', 'MAX_SEARCH_ROUNDS', 'MAX_SEARCH_QUERIES', 'REQUEST_TIMEOUT_SECONDS'])
def test_misconfigured_task_finishes_and_next_task_can_run(monkeypatch, setting):
    api = import_module('backend.app')
    client = TestClient(api.app)
    monkeypatch.setenv(setting, 'invalid-private-value')
    def submit():
        task_id = client.post('/api/research', json={'query': 'Research NVIDIA'}).json()['task_id']
        for _ in range(200):
            task = client.get('/api/research/' + task_id).json()
            if task['status'] in {'failed', 'partial', 'completed'}:
                return task
            time.sleep(.01)
        pytest.fail('Task remained pending')
    task = submit()
    assert task['status'] == 'failed' and task['error_code'] == 'INVALID_RESEARCH_CONFIGURATION'
    assert task['error_message'] == setting + ' must be an integer.'
    from backend.storage.store import load_all
    assert load_all()[0][task['task_id']]['status'] == 'failed'
    monkeypatch.delenv(setting)
    assert submit()['status'] == 'partial'


@pytest.mark.parametrize('value', ['0', '-1', '6'])
def test_numeric_configuration_preserves_existing_bounds(monkeypatch, value):
    monkeypatch.setenv('MAX_MODEL_CALLS', value)
    assert ResearchLimits().max_model_calls == int(value)


def test_annual_question_is_partial_through_normal_pipeline(monkeypatch):
    agent = pipeline()
    monkeypatch.setattr(Planner, 'plan', lambda self, query: {**PLAN, 'metrics': ['revenue'], 'comparison': 'latest'})
    monkeypatch.setattr(agent.router.financial, 'directory', lambda timeout: [
        dict(name='NVIDIA Corporation', ticker='NVDA', cik='0001045810')])
    monkeypatch.setattr(agent.router.financial, 'facts', lambda cik, cutoff, **kw: [
        fact('revenue', 100, '2025-01-01', '2025-03-31', cik=cik)])
    report = agent.run_agent('NVIDIA full year revenue', ['NVDA'])
    assert report.status == 'partial'
    assert any('requested year revenue' in gap for gap in report.research_quality.core_gaps)
    assert report.metrics[0].value == 100


@pytest.mark.parametrize('label', ['Conclusion', 'investment-view', 'Recommendation', 'investment thesis'])
def test_conclusion_labels_do_not_lose_stance_or_citations(label):
    import json
    from backend.providers.live import _decode_investment_draft
    draft, partial = _decode_investment_draft(json.dumps(dict(
        stance='cautiously_bearish', confidence='medium', points=[dict(
            topic=label, text='Cash flow risks support a cautious negative view.', citations=['s'])])))
    assert not partial and draft.points[0].topic == 'thesis'
    assert draft.stance == 'cautiously_bearish' and draft.points[0].evidence_ids == ['s']

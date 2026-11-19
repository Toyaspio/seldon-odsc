import pytest

from backend.providers.interfaces import MockModelProvider, MockSearchProvider
from backend.providers.live import NebiusModelProvider, ProviderError, TavilySearchProvider
from backend.providers.sec import SecFinancialDataProvider
from backend.tests.test_phase2 import Facts, PLAN, pipeline


def live_agent(monkeypatch):
    agent = pipeline()
    agent.planner.model = NebiusModelProvider()
    agent.router.search = TavilySearchProvider()
    agent.router.financial = SecFinancialDataProvider()
    monkeypatch.setattr(agent.planner.model, 'plan', lambda *args, **kw: PLAN)
    monkeypatch.setattr(agent.planner.model, 'select_evidence', lambda *args: [])
    monkeypatch.setattr(agent.router.financial, 'directory', Facts().directory)
    monkeypatch.setattr(agent.router.financial, 'facts', Facts().facts)
    monkeypatch.setattr(agent.router.search, 'search',
                        lambda *args, **kw: [{'url': 'https://www.sec.gov/Archives/test'}])
    return agent


@pytest.mark.parametrize('variant,expected', [('full', 'live'), ('mixed', 'mixed'),
                                              ('empty', 'mixed'), ('failed', 'mixed')])
def test_provenance_depends_on_actual_results(monkeypatch, variant, expected):
    agent = live_agent(monkeypatch)
    if variant == 'mixed':
        agent.planner.model = MockModelProvider()
        agent.router.search = MockSearchProvider()
    elif variant == 'empty':
        monkeypatch.setattr(agent.router.search, 'search', lambda *args, **kwargs: [])
    elif variant == 'failed':
        def fail_search(*args, **kwargs):
            raise ProviderError('fixture search error')
        monkeypatch.setattr(agent.router.search, 'search', fail_search)
    report = agent.run_agent('Review ACME cash flows', ['ACME'])
    assert report.data_mode == expected
    assert report.metrics
    assert report.providers['sec'].succeeded >= 2
    search = report.providers['tavily']
    assert not report.providers['esef'].enabled
    assert report.providers['esef'].calls == 0
    if variant == 'mixed':
        assert not search.enabled and search.mock_used and search.calls == 0
        assert report.providers['nebius'].mock_used
    elif variant == 'failed':
        assert search.calls == search.failed > 0
    else:
        assert search.calls == search.succeeded > 0


def test_enabled_provider_without_dispatch_is_not_participating(monkeypatch):
    from backend.agent.limits import ResearchLimits

    agent = live_agent(monkeypatch)
    report = agent.run_agent('Review ACME cash flows', ['ACME'], limits=ResearchLimits(max_search_queries=0))
    search = report.providers['tavily']
    assert search.enabled and search.calls == 0 and report.data_mode == 'mixed'


@pytest.mark.parametrize('query', ['What is SenseTime like?', '帮我分析商汤科技最近的进展', 'Review 0020.HK'])
def test_sensetime_listing_aliases_resolve_to_one_identity(query):
    from backend.agent.pipeline import CompanyResolver

    resolver = CompanyResolver()
    match, = resolver.resolve(resolver.mentioned(query) + ['SenseTime Group Inc. (0020.HK)'], [])
    assert match.company_id == 'hkex:0020'
    assert match.company_name == 'SenseTime Group Inc.'
    assert match.ticker == '0020.HK'
    assert match.exchange == 'HKEX' and match.cik is None and match.resolution_status == 'resolved'
    assert resolver.mentioned('How do I use SenseTimer?') == []


def test_sensetime_question_opens_research_without_an_edgar_match(monkeypatch):
    import json
    from datetime import datetime, timezone
    from io import BytesIO
    from unittest.mock import Mock

    from fastapi.testclient import TestClient
    from backend import app as api
    from backend.agent.models import InvestmentAnalysis

    agent = live_agent(monkeypatch)
    model = agent.planner.model
    monkeypatch.setattr(model, 'plan', lambda *_args, **_kw: dict(
        PLAN, companies=['SenseTime Group Inc.', 'Acme Holdings'],
        retrieval_tasks=[{'query': 'SenseTime business update', 'purpose': 'Review company announcements'}]))
    monkeypatch.setattr(agent.router.financial, 'directory',
                        lambda *_args: pytest.fail('The HKEX issuer does not require an SEC directory lookup'))
    monkeypatch.setattr(agent.router.financial, 'facts',
                        lambda *_args, **_kw: pytest.fail('Do not send a Hong Kong ticker to SEC Company Facts'))
    date = datetime.now(timezone.utc).date()
    url = 'https://sensetime.com/en/news/sensetime-business-update-revenue-growth-20261118-1945'
    excerpt = 'SenseTime provided a business update for the nine months ended September 30, 2026.'
    searched = []

    def search(request, timeout):
        searched.append(json.loads(request.data)['query'])
        return BytesIO(json.dumps({'results': [
            {'url': url, 'title': 'Company business update', 'content': excerpt,
             'published_date': date.isoformat()},
        ]}).encode())

    def verify_publisher(source, quote, trusted_hosts, cutoff, timeout):
        assert source.source_type == 'issuer_lead' and source.company_id == 'hkex:0020'
        assert {'sensetime.com', 'www1.hkexnews.hk'} <= trusted_hosts
        return True, date

    def news(context, *_args, **_kw):
        key = context['search_results'][0]['source_id']
        return json.dumps({'overall_assessment': 'neutral', 'selections': [
            {'source_id': key, 'category': 'operations_strategy', 'summary': 'SenseTime issued a business update.',
             'excerpt': excerpt, 'attribution': 'management_statement'},
        ]})

    def news_article(context, *_args, **_kw):
        return json.dumps({'overall_assessment': 'neutral', 'insights': [
            {'kind': 'business', 'text': 'The update provides a starting point for evaluating disclosed progress.',
             'evidence_ids': [context['checked_news'][0]['source_id']]},
        ]})

    def investment(context, _limits):
        return InvestmentAnalysis(stance='provisional_neutral', confidence='low', missing_inputs=[],
                                  points=[{'topic': 'outlook', 'text': 'Assess progress against future disclosures.',
                                           'evidence_ids': [context['unverified_external_leads'][0]['source_id']]}])

    agent.router.search = TavilySearchProvider(api_key='fixture', opener=search)
    monkeypatch.setattr('backend.agent.news._publisher_check', verify_publisher)
    monkeypatch.setattr(model, 'synthesize_news', news)
    monkeypatch.setattr(model, 'synthesize_news_article', news_article)
    monkeypatch.setattr(model, 'synthesize_investment', investment)
    monkeypatch.setattr(model, 'review_batch', lambda payload, *_args: json.dumps({
        'verdicts': [{'item_id': row['item_id'], 'verdict': 'supported'} for row in payload['items']],
    }))
    monkeypatch.setattr(api, '_tasks', {})
    monkeypatch.setattr(api, '_reports', {})
    monkeypatch.setattr(api, '_live_enabled', True)
    monkeypatch.setattr(api, '_orchestrator', agent)
    monkeypatch.setattr(api, '_model_provider', model)
    worker = Mock()
    worker.submit.side_effect = lambda fn, *args: fn(*args)
    monkeypatch.setattr(api, '_executor', worker)
    client = TestClient(api.app)
    created = client.post('/api/research', json={'query': 'What is SenseTime like?'})
    assert created.status_code == 202
    task = client.get('/api/research/' + created.json()['task_id']).json()
    assert task['status'] == 'partial' and task['progress'] == 100, task
    report = client.get('/api/reports/' + task['report_id']).json()
    assert [company['company_id'] for company in report['companies']] == ['hkex:0020']
    assert any('0020.HK recent announcements' in query for query in searched)
    assert report['data_mode'] == 'mixed' and not report['metrics']
    assert report['providers']['sec']['calls'] == 0
    assert report['news_analysis']['generation_status'] == 'synthesis_completed'
    assert any('no structured financial feed is configured for HKEX' in note for note in report['limitations'])


def test_model_plan_keeps_useful_fields_when_optional_hints_vary():
    import json
    from io import BytesIO

    response = {
        'core_question': 'How will earnings affect the market?',
        'companies': ['NVIDIA'],
        'metrics': [' REVENUE ', 'market_expectations', 'revenue'],
        'preferred_source_types': 'EARNINGS_RELEASE',
        'comparison': 'versus market expectations',
        'retrieval_tasks': [{'query': 'NVIDIA earnings release', 'priority': 'high'}],
        'methodology': 'Compare operating figures with expectations.',
    }
    completion = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(response)}}]}
    model = NebiusModelProvider(api_key='fixture',
                                opener=lambda *_args, **_kwargs: BytesIO(json.dumps(completion).encode()))
    plan = model.plan('How will Nvidia’s new earnings report affect the market?')
    assert plan['metrics'] == ['revenue']
    assert plan['preferred_source_types'] == ['earnings_release']
    assert plan['comparison'] == 'latest'
    assert plan['retrieval_tasks'][0]['query'] == 'NVIDIA earnings release'


@pytest.mark.parametrize('search_fails', [False, True])
@pytest.mark.parametrize('fault', ['schema', 'planning_529', 'persistent_529'])
def test_model_plan_failure_continues_web_task_when_issuer_is_known(monkeypatch, search_fails, fault):
    import json
    from io import BytesIO
    from unittest.mock import Mock
    from urllib.error import HTTPError
    from fastapi.testclient import TestClient

    from backend import app as api
    from backend.agent.models import InvestmentAnalysis

    agent = live_agent(monkeypatch)
    model = NebiusModelProvider(api_key='fixture')
    agent.planner.model = model
    calls = []

    def broken_model(request, timeout):
        calls.append(request.full_url)
        if fault != 'schema':
            raise HTTPError(request.full_url, 529, 'fixture upstream error', {}, None)
        return BytesIO(b'{"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]}')

    model.opener = broken_model
    monkeypatch.setattr('backend.providers.live.time.sleep', lambda _delay: None)
    monkeypatch.setattr(agent.router.financial, 'directory', lambda *_args: [
        {'name': 'NVIDIA Corporation', 'ticker': 'NVDA', 'cik': '0001045810', 'exchange': 'Nasdaq'},
    ])
    def facts(cik, cutoff, *, timeout_seconds, include_valuation=False):
        return Facts().facts(cik, cutoff, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(agent.router.financial, 'facts', facts)
    searched = []

    def search(query, timeout, **kwargs):
        searched.append(query)
        if search_fails:
            raise TimeoutError
        return [{'url': 'https://www.sec.gov/Archives/fixture', 'title': 'Fixture filing'}]

    def synthesize(context, limits):
        evidence = context['verified_financial_facts'][0]['evidence_ids']
        return InvestmentAnalysis(stance='neutral', confidence='medium', missing_inputs=[],
                                  points=[{'topic': 'thesis', 'text': 'Operating cash flow supports ongoing operations.',
                                           'evidence_ids': evidence}])

    if fault != 'persistent_529':
        model.investment_follow_up = lambda *_args: []
        model.synthesize_investment = synthesize
        model.review_batch = lambda payload, *_args: json.dumps({
            'verdicts': [{'item_id': item['item_id'], 'verdict': 'supported'} for item in payload['items']],
        })
    monkeypatch.setattr(agent.router.search, 'search', search)
    monkeypatch.setattr(api, '_tasks', {})
    monkeypatch.setattr(api, '_reports', {})
    monkeypatch.setattr(api, '_live_enabled', True)
    monkeypatch.setattr(api, '_orchestrator', agent)
    monkeypatch.setattr(api, '_model_provider', model)
    monkeypatch.setattr(api, '_executor', Mock(**{'submit.side_effect': lambda fn, *args: fn(*args)}))
    client = TestClient(api.app)
    response = client.post('/api/research', json={'query': 'How will Nvidia’s new earnings report affect the market?'})
    assert response.status_code == 202
    task = client.get('/api/research/' + response.json()['task_id']).json()
    assert task['status'] in {'completed', 'partial'} and task['progress'] == 100, task
    report = client.get('/api/reports/' + task['report_id']).json()
    assert searched and report['metrics']
    assert any('Research continued' in item for item in report['limitations'])
    if fault == 'persistent_529':
        assert report['data_mode'] == 'mixed'
        assert report['investment_review']['generation'] == 'generation_failed'
        assert not report['investment_analysis']['points']
        assert report['providers']['nebius']['succeeded'] == 0
    else:
        assert report['investment_review']['opinion'] == 'approved'
        assert len(calls) == (1 if fault == 'schema' else 3)
    if search_fails or fault == 'persistent_529':
        assert report['status'] == 'partial'
    event = next(event for event in report['provider_events'] if event['stage'] == 'planning')
    expected_attempts = 1 if fault == 'schema' else 3
    assert event['outcome'] == 'failed' and event['http_attempts'] == event['http_responses'] == expected_attempts
    assert event['error_code'] == ('invalid_research_plan' if fault == 'schema' else 'HTTP 529')


@pytest.mark.parametrize('indexed,metadata,accepted', [
    ('2025-07-31', '2025-07-31', True),
    ('Fri, 01 Aug 2025 17:00:00 GMT', '2025-08-01', True),
    ('2025-08-02', '2025-08-02', False),
    (None, '2025-07-31', False),
    ('2025-07-31', None, False),
    ('2025-07-31', '2025-08-02', False),
    ('2025-07-30', '2025-07-31', False),
])
def test_historical_source_dates(monkeypatch, indexed, metadata, accepted):
    from datetime import date
    from email.message import Message
    from io import BytesIO

    from backend.agent.limits import ResearchLimits
    from backend.agent.pipeline import historical_leads

    class Page(BytesIO):
        headers = Message()
        headers['Content-Type'] = 'text/html'
    html = f'<meta property="article:published_time" content="{metadata}">' if metadata else ''
    monkeypatch.setattr('backend.providers.historical.open_provider_request',
                        lambda *args: Page(html.encode()))
    url = 'https://www.sec.gov/Archives/test'
    incoming = {'url': url, 'published_date': indexed, 'content': 'Mutable copy', 'title': 'Mutable title'}
    leads = historical_leads([incoming], date(2025, 8, 1), {'www.sec.gov'}, ResearchLimits(), {}, [])
    if accepted:
        assert len(leads) == 1 and leads[0] == {'url': url, 'published_date': metadata}
    else:
        assert leads == []


def test_tavily_requests_an_inclusive_historical_cutoff_without_changing_current_search():
    import json
    from io import BytesIO

    bodies = []

    def response(request, timeout):
        bodies.append(json.loads(request.data))
        return BytesIO(b'{"results": []}')

    search = TavilySearchProvider(api_key='fixture', opener=response)
    search.search('test', 1, as_of_date='2025-08-01')
    search.search('test', 1)
    assert bodies[0]['end_date'] == '2025-08-02'
    assert bodies[0]['include_published_date'] and bodies[0]['filter_by_published_date']
    assert set(bodies[0]) - set(bodies[1]) == {
        'end_date', 'include_published_date', 'filter_by_published_date'
    }


def test_follow_up_and_model_selection_respect_as_of(monkeypatch):
    from datetime import date

    agent = live_agent(monkeypatch)
    calls = []
    def search(query, timeout, *, as_of_date=None):
        calls.append(as_of_date)
        return [
            {'url': 'https://www.sec.gov/Archives/old', 'published_date': '2025-08-01',
             'content': 'Current snippet contains future knowledge.'},
            {'url': 'https://www.sec.gov/Archives/future', 'published_date': '2025-07-31',
             'title': 'Future information'},
        ]
    monkeypatch.setattr(agent.router.search, 'search', search)
    monkeypatch.setattr('backend.agent.pipeline.source_publication',
                        lambda url, *_: date(2025, 8, 2) if url.endswith('future') else date(2025, 8, 1))
    def select(*args):
        pytest.fail('Historical results must not pass mutable snippets to model selection')
    monkeypatch.setattr(agent.planner.model, 'select_evidence', select)
    plan_context = []
    def plan(query, **kw):
        plan_context.append(query)
        return PLAN
    monkeypatch.setattr(agent.planner.model, 'plan', plan)
    original = Facts().facts
    def facts(cik, cutoff, *, timeout_seconds):
        assert cutoff == '2025-08-01'
        rows = original(cik, cutoff, timeout_seconds=timeout_seconds)
        return rows + [dict(rows[0], value=333, filed_at='2025-08-02'),
                       dict(rows[0], value=444, filed_at='2025-08-01', period_end='2025-08-02')]
    monkeypatch.setattr(agent.router.financial, 'facts', facts)
    report = agent.run_agent('Review ACME cash flows and AI ROI', ['ACME'], as_of_date='2025-08-01')
    assert len(calls) > 1 and set(calls) == {'2025-08-01'}
    assert '2025-08-01' in plan_context[0]
    web = [source for source in report.sources if source.source_id.startswith('web:')]
    assert len(web) == 1 and web[0].url.endswith('/old')
    assert web[0].quoted_text in (None, '') and web[0].publication_date == '2025-08-01'
    assert not any(source.numeric_value in (333, 444) for source in report.sources)
    assert not any('Future information' in claim.text for claim in report.claims)


def test_historical_publisher_fetch_rejects_unknown_hosts_and_post_cutoff_edits(monkeypatch):
    from datetime import date
    from email.message import Message
    from io import BytesIO

    from backend.providers.historical import source_publication

    fetched = []
    class Page(BytesIO):
        headers = Message()
        headers['Content-Type'] = 'text/html'
    def open_page(request, timeout):
        fetched.append(request.full_url)
        return Page(b'<meta property="article:published_time" content="2025-07-31">'
                    b'<meta property="article:modified_time" content="2025-08-02">')
    monkeypatch.setattr('backend.providers.historical.open_provider_request', open_page)
    day = date(2025, 8, 1)
    assert source_publication('https://example.org/test', {'www.sec.gov'}, day, 1) is None
    assert fetched == []
    assert source_publication('https://www.sec.gov/test', {'www.sec.gov'}, day, 1) is None
    assert fetched == ['https://www.sec.gov/test']


def test_conflicting_claims_keep_both_citations_without_a_confirmed_value():
    from backend.evidence.verifier import verify_claim
    from backend.tests.test_qa_phase25 import claim, source

    primary = source()
    competing = source(source_id='sec:other', numeric_value=250.0)
    result = verify_claim(claim(), [primary, competing])
    assert result.verification_status == 'contradicted'
    assert result.text.startswith('Evidence conflict')
    assert 'disclosed' not in result.text and '250' not in result.text
    assert result.numeric_value is None and result.claim_type == 'evidence_gap'
    assert set(result.evidence_ids) == {primary.source_id, competing.source_id}


def test_missing_evidence_disclosure_is_reworded():
    from backend.evidence.verifier import verify_claim
    from backend.tests.test_qa_phase25 import claim

    checked = verify_claim(claim(), [])
    assert checked.verification_status == 'insufficient'
    assert checked.text.startswith('Unverified')
    assert 'disclosed' not in checked.text
    assert checked.numeric_value is None and checked.actual_or_guidance is None


def test_legacy_live_report_is_relabelled_without_rewriting_storage(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import app as api
    from backend.tests.test_qa_phase25 import claim

    fixture = pipeline().run_agent('Review ACME cash flows', ['ACME']).model_dump(mode='json')
    fixture.pop('providers')
    fixture['data_mode'] = 'live'
    fixture['data_as_of'] = '2025-08-01'
    fixture['claims'].append(claim(limitations=['Conflicting values for the same measure and period.'],
                                   evidence_ids=[]).model_dump())
    monkeypatch.setitem(api._reports, fixture['report_id'], fixture)
    response = TestClient(api.app).get(f"/api/reports/{fixture['report_id']}")
    assert response.status_code == 200
    repaired = response.json()
    assert repaired['data_mode'] == 'unverified'
    assert all(not source['source_id'].startswith('web:') for source in repaired['sources'])
    assert repaired['claims'][-1]['text'].startswith('Evidence conflict')
    assert fixture['data_mode'] == 'live'


def test_configured_live_route_exposes_per_task_results_and_schema(monkeypatch):
    import json
    from pathlib import Path

    from fastapi.testclient import TestClient
    from jsonschema import validate

    from backend import app as api
    from backend.tests.test_edge_cases import _terminal

    monkeypatch.setattr(api, '_live_enabled', True)
    monkeypatch.setattr(api, '_orchestrator', live_agent(monkeypatch))
    with TestClient(api.app) as client:
        request = client.post('/api/research', json={'query': 'Review ACME cash flows', 'companies': ['ACME']})
        assert request.status_code == 202
        task = _terminal(client, request.json()['task_id'])
        report = client.get('/api/reports/' + task['report_id']).json()
        assert report['data_mode'] == 'live'
        assert report['providers'] == task['providers']
        assert set(report['providers']) == {'nebius', 'tavily', 'sec', 'esef'}
        schema = Path('contracts/report.schema.json').read_text(encoding='utf-8')
        validate(report, json.loads(schema))


def test_search_failure_after_success_preserves_evidence_and_activity(monkeypatch):
    agent = live_agent(monkeypatch)
    batches = iter([[{'url': 'https://www.sec.gov/Archives/test', 'content': 'Search lead'}]])
    def search(*args, **kwargs):
        try:
            return next(batches)
        except StopIteration:
            raise ProviderError('fixture follow-up error') from None
    monkeypatch.setattr(agent.router.search, 'search', search)
    report = agent.run_agent('Review ACME cash flows and AI ROI', ['ACME'])
    assert report.data_mode == 'live' and report.status == 'partial'
    assert report.providers['tavily'].succeeded == 1
    assert report.providers['tavily'].failed > 0
    assert report.metrics and any(s.source_id.startswith('web:') for s in report.sources)


def test_failed_model_attempt_is_available_on_the_task(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import app as api
    from backend.tests.test_edge_cases import _terminal

    def fail(*args, **kwargs):
        raise ProviderError('fixture model error')
    agent = live_agent(monkeypatch)
    monkeypatch.setattr(agent.planner.model, 'plan', fail)
    monkeypatch.setattr(api, '_live_enabled', True)
    monkeypatch.setattr(api, '_orchestrator', agent)
    with TestClient(api.app) as client:
        response = client.post('/api/research', json={'query': 'Review ACME cash flows'})
        task = _terminal(client, response.json()['task_id'])
        assert task['status'] == 'partial' and task['report_id']
        report = client.get('/api/reports/' + task['report_id']).json()
    assert any(event['stage'] == 'planning' and event['outcome'] == 'failed'
               for event in report['provider_events'])
    assert task['providers']['tavily']['enabled'] and task['providers']['tavily']['calls'] > 0

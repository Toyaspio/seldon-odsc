from copy import deepcopy
from datetime import date
from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from backend.agent.limits import ResearchLimits
from backend.agent.models import Company, Metric, Source
from backend.agent.news import research_news
from backend.agent.pipeline import ToolRouter
from backend.evidence.extractor import EvidenceExtractor
from backend.evidence.registry import EvidenceRegistry
from backend.providers.live import NebiusModelProvider, TavilySearchProvider


COMPANY = Company(company_id='nvda', company_name='NVIDIA', ticker='NVDA', resolution_status='resolved')
PRIMARY = 'https://nvidianews.nvidia.com/news/fixture-results'
MEDIA = 'https://outlet.example/news/fixture-market'
THIRD = 'https://more.example/other'
CLOSE = date(2026, 11, 19)
PRIMARY_QUOTE = 'Management announced a new production program on Nov. 17, 2026.'
MEDIA_QUOTE = 'An analyst said competing products could restrain customer demand.'
FACT = Metric(metric_id='fact', company_id='nvda', metric_name='operating_cash_flow',
              value=10, unit='USD', currency='USD', period_start='2026-07-27',
              period_end='2026-10-25', fiscal_period='Q3', accounting_basis='US GAAP',
              actual_or_guidance='actual', duration_kind='quarter', evidence_ids=['sec:cash'])
FACT_SOURCE = Source(source_id='sec:cash', title='Fixture filing', publisher='SEC',
                     url='https://www.sec.gov/fixture', source_type='sec_xbrl')


class Document(BytesIO):
    status = 200

    class headers:
        @staticmethod
        def get_content_type():
            return 'text/html'


def _json_response(data):
    return BytesIO(json.dumps(data).encode())


@pytest.mark.parametrize('same_origin', [True, False])
def test_official_publisher_canonical_redirect_stays_on_same_origin(monkeypatch, same_origin):
    from backend.agent.news import _publisher_check

    url = 'https://investor.nvidia.com/results'
    source = Source(source_id='web:issuer', title='Issuer earnings', publisher='NVIDIA', url=url,
                    source_type='issuer_lead')
    requests = []

    def open_page(request, timeout):
        requests.append(request.full_url)
        if len(requests) == 1:
            target = '/results/' if same_origin else 'https://elsewhere.example/results/'
            raise HTTPError(url, 301, 'Moved', {'Location': target}, BytesIO())
        return Document(b'<html><body>The issuer reported its results.</body></html>')

    monkeypatch.setattr('backend.agent.news.open_provider_request', open_page)
    matched, _ = _publisher_check(source, 'The issuer reported its results.',
                                  {'investor.nvidia.com'}, CLOSE, 5)
    assert matched is same_origin
    assert requests == ([url, url + '/'] if same_origin else [url])


def _search(rows, limits):
    seen = []
    def opener(request, timeout):
        seen.append(json.loads(request.data))
        return _json_response({'results': rows})
    limits.providers['tavily'].enabled = True
    router = ToolRouter(TavilySearchProvider(api_key='fixture', opener=opener), None)
    results = router.search_round(['NVIDIA products and competition'], limits,
                                  stages={'NVIDIA products and competition':'news_discovery'})
    assert seen[0]['topic'] == 'news' and seen[0]['search_depth'] == 'basic'
    assert limits.events[0].http_attempts == 1
    return EvidenceExtractor().web(results, [COMPANY], {'nvda':'nvidia.com'})


def test_live_adapter_to_model_to_news_report_with_grounded_financial_evidence(monkeypatch):
    limits = ResearchLimits()
    sources = _search([
        {'url': PRIMARY, 'title': 'Issuer release', 'content': PRIMARY_QUOTE,
         'published_date': '2026-11-18'},
        {'url': MEDIA, 'title': 'Analyst comments', 'content': MEDIA_QUOTE,
         'published_date': '2026-11-19'},
        {'url': THIRD, 'title': 'Unselected item', 'content': 'Candidate text without material new facts.'},
    ], limits)
    by_url = {item.url: item.source_id for item in sources}
    primary_id, media_id = by_url[PRIMARY], by_url[MEDIA]
    checked = []

    def publisher(request, timeout):
        checked.append(request.full_url)
        return Document(('<html><head><meta property="article:published_time" content="2026-11-18">'
                         '</head><body>' + PRIMARY_QUOTE + '</body></html>').encode())

    def model(request, timeout):
        body = json.loads(request.data)
        context = json.loads(body['messages'][1]['content'])
        assert body['max_tokens'] == 6144
        if 'checked_news' in context:
            assert {item['source_id'] for item in context['checked_news']} == {primary_id, media_id}
            assert {item['kind'] for item in context['analysis_points']} == {'positive', 'negative', 'cross_news'}
            return _json_response({'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
                'overall_assessment':'mixed',
                'insights':[{'kind':'synthesis','title':'A heading with no body'},
                            {'kind':'business','text':'Weigh capacity against future cash conversion.',
                             'evidence_ids':[primary_id, media_id, FACT.metric_id]}],
            })}}]})
        assert {item['source_id'] for item in context['search_results']} == set(by_url.values())
        assert context['verified_financial_facts'][0]['value'] == 10
        assert context['verified_financial_facts'][0]['evidence_ids'] == ['sec:cash']
        draft = {'overall_assessment':'mixed', 'selections':[
            {'source_id':primary_id, 'title':'Malformed selection'},
            {'source_id':primary_id,'category':'products_technology','summary':'Production plan announced.',
             'excerpt':PRIMARY_QUOTE, 'event_date':'2026-11-17','attribution':'management_statement',
             'related_source_ids':[media_id]},
            {'source_id':media_id,'category':'analyst_views','summary':'Analyst flagged a possible demand risk.',
             'excerpt':MEDIA_QUOTE,'event_date':'2026-11-19','attribution':'analyst_view'},
            {'source_id':by_url[THIRD],'category':'other','summary':'Fabricated anchor.',
             'excerpt':'This text was never returned by the publisher.', 'attribution':'reported_event'},
        ], 'insights':[
            {'kind':'positive','text':'A broader production program could serve additional orders.','evidence_ids':[primary_id]},
            {'kind':'negative','text':'Competition could constrain returns.','evidence_ids':[media_id]},
            {'kind':'cross_news','text':'Capacity growth helps only if competitive demand persists.','evidence_ids':[primary_id, media_id]},
            {'kind':'synthesis','text':'I would weigh new capacity against future cash conversion.',
             'evidence_ids':[primary_id, media_id, 'sec:cash']},
            {'kind':'synthesis','text':'This invented reference is invalid.','evidence_ids':['missing', primary_id]},
            {'kind':'cross_news','text':'One publication alone does not establish a cross-news link.','evidence_ids':[primary_id]},
        ]}
        return _json_response({'choices':[{'message':{'content':json.dumps(draft)}}]})

    monkeypatch.setattr('backend.agent.news.open_provider_request', publisher)
    limits.providers['nebius'].enabled = True
    before = deepcopy(FACT.model_dump())
    report = research_news('NVIDIA outlook', {'nvda':'NVIDIA'}, sources, [FACT],
                           NebiusModelProvider(api_key='fixture', opener=model), limits, CLOSE,
                           {'nvidianews.nvidia.com'}, registry=EvidenceRegistry([*sources, FACT_SOURCE], [FACT]))
    assert FACT.model_dump() == before
    assert report.status == 'completed' and report.confidence == 'medium'
    assert [item.kind for item in report.insights] == ['positive', 'negative', 'cross_news', 'synthesis']
    assert report.insights[-1].evidence_ids == [primary_id, media_id, 'sec:cash']
    assert 'Some incomplete model items were omitted.' in report.limitations
    assert report.news_items[0].event_date == '2026-11-17'
    assert report.news_items[0].publication_date == '2026-11-18'
    assert report.news_items[0].publisher_excerpt_matched and checked == [PRIMARY]
    assert report.news_items[0].attribution == 'management_statement'
    assert report.news_items[1].attribution == 'analyst_view' and report.news_items[1].event_date is None
    event = limits.events[0]
    assert len(event.news_context_source_ids) == 3
    assert len(event.news_selected_source_ids) == 2
    assert len(event.news_cited_source_ids) == 2


def test_nebius_failure_preserves_live_search_results():
    limits = ResearchLimits()
    sources = _search([{'url': MEDIA, 'title':'Analyst comments', 'content':MEDIA_QUOTE}], limits)
    limits.providers['nebius'].enabled = True
    def fail(request, timeout):
        raise HTTPError(request.full_url, 401, '', {}, None)
    result = research_news('NVIDIA', {'nvda':'NVIDIA'}, sources, [],
                           NebiusModelProvider(api_key='fixture', opener=fail), limits, CLOSE, set(),
                           registry=EvidenceRegistry(sources))
    assert result.status == 'partial' and result.confidence == 'low'
    assert result.news_items[0].attribution == 'unclassified'
    assert result.insights == []
    assert limits.events[0].news_context_source_ids == [sources[0].source_id]
    assert limits.events[0].news_selected_source_ids == []


@pytest.mark.parametrize(('fail','expected'),[(False,'no_results'),(True,'search_failed')])
def test_no_usable_news_never_invokes_nebius(fail, expected):
    limits = ResearchLimits()
    limits.providers['tavily'].enabled = True
    def opener(request, timeout):
        if fail:
            raise HTTPError(request.full_url, 401, '', {}, None)
        return _json_response({'results': []})
    router = ToolRouter(TavilySearchProvider(api_key='fixture', opener=opener), None)
    router.search_round(['NVIDIA'], limits, [], stages={'NVIDIA':'news_discovery'})
    def no_model(*args):
        pytest.fail('No model call without live evidence')
    result = research_news('NVIDIA', {'nvda':'NVIDIA'}, [], [],
                           NebiusModelProvider(api_key='fixture', opener=no_model), limits, CLOSE, set(),
                           registry=EvidenceRegistry([]))
    assert result.status == expected and not result.news_items and not result.insights


@pytest.mark.parametrize(('faults','expected','stages'), [
    (['length', None],
     'synthesis_completed', ['news_analysis', 'news_analysis_retry', 'news_article']),
    (['length', 'length'],
     'generation_truncated', ['news_analysis', 'news_analysis_retry']),
    ([None, 'length', None],
     'synthesis_completed', ['news_analysis', 'news_article', 'news_article_retry']),
    ([None, 'length', 'length'],
     'generation_truncated', ['news_analysis', 'news_article', 'news_article_retry']),
    ([None, 'malformed'],
     'generation_failed', ['news_analysis', 'news_article']),
])
def test_news_stage_truncation_restores_only_failed_stage(monkeypatch, tmp_path, faults, expected, stages):
    import sqlite3
    from backend.storage import store

    monkeypatch.setenv('SELDON_DATA_DIR', str(tmp_path))
    limits = ResearchLimits()
    sources = _search([{'url': MEDIA, 'title':'Analyst comments', 'content':MEDIA_QUOTE,
                        'published_date':'2026-11-19'}], limits)
    limits.providers['nebius'].enabled = True
    reference = sources[0].source_id
    issues = iter(faults)

    def opener(request, timeout):
        body = json.loads(request.data)
        context = json.loads(body['messages'][1]['content'])
        assert body['max_tokens'] == 6144
        is_article = 'checked_news' in context
        phase = 'news_analysis_completed' if is_article else 'news_collected'
        with sqlite3.connect(tmp_path / 'tasks.sqlite3') as db:
            assert db.execute('SELECT stage FROM news_checkpoints WHERE stage=?', (phase,)).fetchone()
        marker = next(issues, None)
        if marker:
            return _json_response({'choices':[{'finish_reason':'length' if marker == 'length' else 'stop',
                                              'message':{'content':'{"overall_assessment":'}}],
                                   'usage':{'prompt_tokens':40,'completion_tokens':3072}})
        insights = [{'kind':'synthesis' if is_article else 'negative',
                     'text':'Reported competition could constrain demand.', 'evidence_ids':[reference]}]
        result = {'overall_assessment':'cautious','insights':insights}
        if not is_article:
            result['selections'] = [{'source_id':reference,'category':'analyst_views',
                                     'summary':'Competition may weigh on demand.',
                                     'excerpt':MEDIA_QUOTE,'attribution':'analyst_view'}]
        return _json_response({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]})

    result = research_news('NVIDIA', {'nvda':'NVIDIA'}, sources, [],
            NebiusModelProvider(api_key='fixture', opener=opener), limits, CLOSE, set(),
            registry=EvidenceRegistry(sources),
            checkpoint=lambda stage, snapshot: store.save_news_checkpoint('test-task', stage, snapshot))
    assert result.generation_status == expected
    assert result.news_items
    assert [event.stage for event in limits.events if event.provider == 'nebius'] == stages
    for event in limits.events:
        if event.finish_reason == 'length':
            assert event.outcome == 'failed' and event.error_code == 'OUTPUT_TRUNCATED'
            assert event.http_responses == 1
    assert limits.queries_used == 1
    if stages[-1].startswith('news_article') and expected != 'synthesis_completed':
        assert result.failed_stage == 'article'
        assert [i.kind for i in result.insights] == ['negative']


def test_news_retry_respects_reserved_model_call():
    limits = ResearchLimits(max_model_calls=2)
    sources = _search([{'url': MEDIA, 'title':'Analyst comments', 'content':MEDIA_QUOTE}], limits)
    limits.providers['nebius'].enabled = True
    def opener(request, timeout):
        return _json_response({'choices':[{'finish_reason':'length','message':{'content':'{'}}]})
    result = research_news('NVIDIA', {'nvda':'NVIDIA'}, sources, [],
                           NebiusModelProvider(api_key='fixture', opener=opener), limits, CLOSE, set(),
                           registry=EvidenceRegistry(sources),
                           reserve_model_calls=1)
    assert result.generation_status == 'generation_truncated'
    assert result.news_items and limits.model_calls == 1


def test_news_prose_and_truncated_json_keep_independent_cited_paragraphs():
    from backend.agent.news import NewsArticle, _decode_news_output

    prose = 'Sales could improve according to web:known.\n\nUncited claim.'
    report = _decode_news_output(prose, NewsArticle, ['web:known'])
    assert len(report.insights) == 1
    assert report.insights[0].evidence_ids == ['web:known']
    truncated = '{"insights":[{"text":"A complete paragraph","evidence_ids":["web:known"]},{"text":"incomplete'
    report = _decode_news_output(truncated, NewsArticle, partial=True)
    assert [point.text for point in report.insights] == ['A complete paragraph']

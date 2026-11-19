from io import BytesIO
import importlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.agent.investment import analyze_investment
from backend.agent.investment_review import investment_article, run_review
from backend.agent.limits import ResearchLimits
from backend.providers.live import NebiusModelProvider


def input_facts():
    row = dict(company_id='issuer', metric_name='operating_cash_flow', duration_kind='quarter',
               period_end='2026-10-25', actual_or_guidance='actual', value=100,
               currency='USD', unit='USD', accounting_basis='US GAAP', capex_scope=None,
               metric_id='fact:cash', evidence_ids=['sec:verified'])
    return SimpleNamespace(**row, model_dump=lambda **kw: row)


def response(result, reason='stop'):
    return BytesIO(json.dumps({'choices':[{'finish_reason':reason,
                                 'message':{'content':json.dumps(result, ensure_ascii=False)}}]}).encode())


@pytest.mark.parametrize('stance', ['bullish', 'bearish', 'neutral'])
def test_english_reviewed_article_for_chinese_question_without_market_data(monkeypatch, tmp_path, stance):
    api = importlib.import_module('backend.app')
    monkeypatch.setenv('SELDON_DATA_DIR', str(tmp_path))
    plan = SimpleNamespace(core_question='经营前景如何？', verification_questions=[])
    text = {
        'bullish': 'I am bullish on the fundamentals because operating cash flow gives the company room to expand.',
        'bearish': 'I am bearish on the fundamentals because positive cash flow alone cannot prove expansion will pay off.',
        'neutral': 'I remain neutral. Disclosed cash flow provides a buffer, but returns on expansion depend on new demand.',
    }
    paragraph_text = [
        ('operating_state', 'Operating cash flow is positive, which provides a starting point for assessing expansion.'),
        ('thesis', text[stance]),
        ('supporting_case', 'Continued cash collections could fund expansion.'),
        ('opposing_case', 'Additional capacity could put pressure on cash spending.'),
        ('bull_case', 'If orders continue to turn into cash, the company could have more room to invest.'),
        ('base_case', 'If demand holds steady, monitor whether cash flow continues to cover investment.'),
        ('bear_case', 'If payment cycles lengthen, new sales could be slower to turn into cash.'),
        ('invalidation', 'If disclosed receivables turnover worsens, I would reassess cash flow quality.'),
    ]
    job = []
    limits = ResearchLimits()
    limits.providers['nebius'].enabled = True
    def opener(request, timeout):
        body = json.loads(request.data)
        payload = json.loads(body['messages'][1]['content'])
        assert 'in English, regardless of the language' in body['messages'][0]['content']
        if 'items' not in payload:
            assert payload['article_language'] == 'en'
            assert payload['question'] == '请分析经营影响'
            assert payload['verified_financial_facts'][0]['evidence_ids'] == ['sec:verified']
            assert payload['unverified_external_leads'] == []
            return response({'stance':stance, 'confidence':'medium',
                'points':[{'topic':topic,'text':value,'evidence_ids':['sec:verified']}
                          for topic,value in paragraph_text],
                'missing_inputs':['Current price and consensus estimates are unavailable.']})
        assert payload['article_thesis']
        from backend.storage.store import load_pending_reviews
        _, _, written_job = load_pending_reviews()[0]
        assert [point['text'] for point in written_job['draft']['points']] == [value for _, value in paragraph_text]
        return response({'verdicts':[{'item_id':item['item_id'],'verdict':'supported',
                                      'short_reason':'The conditional inference cites relevant evidence.'}
                                     for item in payload['items']]})

    model = NebiusModelProvider(api_key='fixture', opener=opener)
    monkeypatch.setattr(api, '_model_provider', model)
    analyze_investment('请分析经营影响', plan, [input_facts()], [SimpleNamespace(source_id='sec:verified')],
                       [], model, limits, Mock(), [], False, on_draft=job.append)
    base = {'limitations':[], 'providers':limits.provider_snapshot(), 'status':'completed', 'research_quality':{}}
    result = api._review_report('fixture-task', base, job[0], limits)
    assert result['investment_analysis']['stance'] == stance
    assert result['investment_analysis']['evidence_coverage'] == 'financial_only'
    assert result['investment_review']['opinion'] == 'approved'
    assert result['investment_narrative']['language'] == 'en'
    assert [row['text'] for row in result['investment_narrative']['sections']] == [value for _,value in paragraph_text]
    assert all(row['review_verdict']=='supported' and row['evidence_ids']==['sec:verified']
               for row in result['investment_narrative']['sections'])


def test_no_evidence_expresses_observation_without_fabricating_claims():
    model = NebiusModelProvider(api_key='fixture', opener=Mock())
    result = analyze_investment('投资', SimpleNamespace(), [], [], [], model, ResearchLimits(), Mock(), [], False)
    assert result.stance == 'insufficient'
    assert result.observation_reason.startswith('The available evidence does not support')
    assert not result.points and not model.opener.called


def test_local_review_suggestion_keeps_original_paragraph_and_pending_others():
    from backend.agent.investment_review import prepare_review
    from backend.agent.models import InvestmentAnalysis

    draft = InvestmentAnalysis(stance='bullish', confidence='medium', missing_inputs=[],
                               points=[{'topic':'thesis','text':'我认为现金回款支撑经营。','evidence_ids':['sec:verified']},
                                       {'topic':'risk','text':'客户需求仍有不确定性。','evidence_ids':['sec:verified']}])
    state = prepare_review(draft, {'article_language':'zh', 'question':'Test',
                                  'verified_financial_facts':[{'company_id':'issuer','evidence_ids':['sec:verified']}],
                                  'unverified_external_leads':[]}, {'issuer'},
                           {'0': ['sec:verified'], '1': ['sec:verified']}, 'fixture')
    def opener(request, timeout):
        payload = json.loads(json.loads(request.data)['messages'][1]['content'])
        if payload['batch_id'].startswith('risks'):
            return response({}, 'length')
        return response({'verdicts':[{'item_id':'0','verdict':'needs_qualification','short_reason':'避免过度外推。',
                   'suggested_revision':'已披露期间的回款提供一定支撑。'}]})
    limits = ResearchLimits()
    limits.providers['nebius'].enabled = True
    _, review = run_review(state, NebiusModelProvider(api_key='fixture', opener=opener), limits, lambda: None)
    article = investment_article(state)
    assert article['language'] == 'zh'
    rows = article['sections']
    assert review.opinion == 'provisional'
    assert state['draft']['stance'] == 'bullish'
    assert rows[0]['text'] == draft.points[0].text and rows[0]['suggested_revision']
    assert rows[1]['text'] == draft.points[1].text and rows[1]['review_verdict'] == 'pending'


@pytest.mark.parametrize('stage', ['synthesize_news', 'synthesize_news_article'])
def test_news_requests_english_without_translating_source_quotes(monkeypatch, stage):
    model = NebiusModelProvider(api_key='fixture')
    context = {'question': '发生了什么？', 'source_excerpt': '公司公布了财报。'}
    complete = Mock(return_value='{}')
    monkeypatch.setattr(model, '_complete', complete)
    getattr(model, stage)(context, ResearchLimits())
    system, payload, _, _ = complete.call_args.args
    assert 'in English, regardless of the language' in system
    assert 'Keep exact source quotations and proper names as supplied' in system
    assert json.loads(payload) == context


def test_incomplete_model_section_preserves_and_reviews_other_paragraphs(monkeypatch, tmp_path):
    api = importlib.import_module('backend.app')
    monkeypatch.setenv('SELDON_DATA_DIR', str(tmp_path))
    complete = [
        {'topic': 'operating_state', 'text': 'Operating cash flow was positive.', 'evidence_ids': ['sec:verified']},
        {'topic': 'thesis', 'text': 'My view is neutral given the limited evidence on demand.',
         'evidence_ids': ['fact:cash']},
    ]
    reviewed = []

    def opener(request, timeout):
        payload = json.loads(json.loads(request.data)['messages'][1]['content'])
        if 'batch_id' not in payload:
            return response({'stance': 'Neutral', 'confidence': 'medium',
                             'points': [complete[0], {'topic': 'base_case', 'title': 'A section heading'}, complete[1]]})
        reviewed.extend(item['text'] for item in payload['items'])
        return response({'verdicts': [{'item_id': item['item_id'], 'verdict': 'supported'}
                                      for item in payload['items']]})

    model = NebiusModelProvider(api_key='fixture', opener=opener)
    monkeypatch.setattr(api, '_model_provider', model)
    limits = ResearchLimits()
    limits.providers['nebius'].enabled = True
    plan = SimpleNamespace(core_question='How did earnings affect the outlook?', verification_questions=[])
    jobs, issues = [], []
    analyze_investment('Research NVIDIA', plan, [input_facts()], [SimpleNamespace(source_id='sec:verified')],
                       [], model, limits, Mock(), issues, False, on_draft=jobs.append)
    base = {'limitations': issues, 'providers': limits.provider_snapshot(), 'status': 'completed',
            'research_quality': {}}
    result = api._review_report('incomplete-draft-fixture', base, jobs[0], limits)
    assert result['investment_review']['generation'] == 'generation_partial'
    assert result['investment_review']['opinion'] == 'approved'
    assert reviewed == [point['text'] for point in complete]
    assert [part['text'] for part in result['investment_narrative']['sections']] == reviewed
    assert any('Usable sections were retained' in note for note in result['limitations'])


@pytest.mark.parametrize('case', ['missing_thesis', 'external_thesis', 'unknown_source', 'no_verified_anchor',
                                  'supported_article', 'unsupported_financial_section'])
def test_existing_review_batch_delivers_a_grounded_position(monkeypatch, tmp_path, case):
    api = importlib.import_module('backend.app')
    monkeypatch.setenv('SELDON_DATA_DIR', str(tmp_path))
    lead = SimpleNamespace(source_id='web:lead', publisher='Fixture', publication_date=None, company_id='issuer',
                           title='Attributed outlook', quoted_text='Management forecasts growth.')
    points = [{'topic': 'operating_state', 'text': 'Operating cash flow stayed positive.',
               'evidence_ids': ['sec:verified']}]
    if case in {'external_thesis', 'supported_article', 'unsupported_financial_section'}:
        points.append({'topic': 'thesis', 'text': 'Management expects further growth.',
                       'evidence_ids': ['web:lead']})
    dispatched = []

    def opener(request, timeout):
        payload = json.loads(json.loads(request.data)['messages'][1]['content'])
        if 'items' not in payload:
            return response({'stance': 'cautiously_bullish', 'confidence': 'medium', 'points': points})
        dispatched.append(payload['batch_id'])
        review = {'verdicts': [{'item_id': item['item_id'], 'verdict':
                                'needs_qualification' if case == 'no_verified_anchor' or (
                                    case == 'unsupported_financial_section' and item['topic'] == 'operating_state')
                                else 'supported'}
                               for item in payload['items']]}
        assert 'position_context' not in payload
        review['position'] = {'stance': 'neutral', 'text': 'Replacement position',
                              'evidence_ids': ['sec:verified']}
        return response(review)

    model = NebiusModelProvider(api_key='fixture', opener=opener)
    monkeypatch.setattr(api, '_model_provider', model)
    limits = ResearchLimits()
    limits.providers['nebius'].enabled = True
    plan = SimpleNamespace(core_question='What is the investment outlook?', verification_questions=[])
    jobs = []
    analyze_investment('Investment outlook', plan, [input_facts()], [SimpleNamespace(source_id='sec:verified')],
                       [lead], model, limits, Mock(), [], False, on_draft=jobs.append)
    base = {'limitations': [], 'providers': limits.provider_snapshot(), 'status': 'completed',
            'research_quality': {}}
    result = api._review_report('position-fixture', base, jobs[0], limits)
    assert len(dispatched) == len(points)
    assert result['investment_draft']['points'] == points
    analysis = result['investment_analysis']
    assert analysis['stance'] != 'neutral'
    if case in {'external_thesis', 'supported_article'}:
        assert result['investment_review']['opinion'] == 'approved'
        assert analysis['stance'] == 'cautiously_bullish'
        assert analysis['confidence'] == 'medium'
    elif case in {'unsupported_financial_section', 'missing_thesis', 'unknown_source'}:
        assert result['investment_review']['opinion'] == 'provisional'
        assert analysis['stance'] == 'cautiously_bullish'
    else:
        assert result['investment_review']['opinion'] == 'evidence_insufficient'
        assert analysis['stance'] == 'insufficient'


@pytest.mark.parametrize('risk_review_fails', [False, True])
def test_review_uses_shared_citations_and_keeps_usable_analysis(monkeypatch, tmp_path, risk_review_fails):
    from urllib.error import HTTPError

    from backend.agent.models import NewsAnalysis, Source
    from backend.evidence.registry import EvidenceRegistry

    api = importlib.import_module('backend.app')
    monkeypatch.setenv('SELDON_DATA_DIR', str(tmp_path))
    fact = input_facts()
    official = Source(source_id='sec:verified', title='Quarterly filing', publisher='SEC',
                      url='https://www.sec.gov/fixture', source_type='sec_xbrl')
    leads = [Source(source_id=f'web:{index}', title=f'Story {index}', publisher='Fixture outlet',
                    url=f'https://example.com/{index}', source_type='web_search',
                    quoted_text='Management described its capacity expansion.')
             for index in range(12)]
    registry = EvidenceRegistry([official, *leads], [fact])
    news = NewsAnalysis(status='completed', as_of='2026-11-19', insights=[
        {'kind': 'business', 'text': 'The capacity plan could raise operating costs.',
         'evidence_ids': ['web:11', 'sec:verified']},
    ])
    draft_points = [
        {'topic': 'thesis', 'text': 'Positive cash flow gives the company room to expand.',
         'evidence_ids': ['fact:cash', 'sec:verified', 'web:11', 'unknown']},
        {'topic': 'thesis', 'text': 'An unsupported market claim.',
         'evidence_ids': ['unknown-only']},
        {'topic': 'risk', 'text': 'Capacity expansion could pressure operating cash flow.',
         'evidence_ids': ['web:11']},
    ]
    seen = {}

    def opener(request, timeout):
        body = json.loads(request.data)
        payload = json.loads(body['messages'][1]['content'])
        if 'batch_id' not in payload:
            leads_by_id = {lead['source_id']: lead for lead in payload['unverified_external_leads']}
            assert 'web:11' in leads_by_id and 'web:10' not in leads_by_id
            assert payload['news_analysis']['insights'][0]['evidence_ids'] == ['web:11', 'sec:verified']
            return response({'stance': 'cautiously_bullish', 'confidence': 'medium',
                             'points': draft_points, 'missing_inputs': []})
        seen[payload['batch_id']] = payload
        if risk_review_fails and payload['batch_id'] == 'risks-1':
            raise HTTPError(request.full_url, 401, '', {}, None)
        return response({'verdicts': [{'item_id': item['item_id'], 'verdict': 'supported'}
                                      for item in payload['items']]})

    model = NebiusModelProvider(api_key='fixture', opener=opener)
    monkeypatch.setattr(api, '_model_provider', model)
    limits = ResearchLimits()
    limits.providers['nebius'].enabled = True
    jobs = []
    plan = SimpleNamespace(core_question='What is the financial outlook?', verification_questions=[])
    analyze_investment('Analyze the company', plan, [fact], [official], leads, model,
                       limits, Mock(), [], False, on_draft=jobs.append, news=news, registry=registry)
    base = {'limitations': [], 'providers': limits.provider_snapshot(), 'status': 'completed', 'research_quality': {}}
    result = api._review_report('shared-citations-fixture', base, jobs[0], limits)
    thesis = seen['thesis-1']
    assert thesis['items'][0]['evidence_ids'] == ['sec:verified', 'web:11']
    assert thesis['items'][0]['unmatched_citation_count'] == 1
    assert [item['item_id'] for item in thesis['items']] == ['0']
    assert [item['metric_id'] for item in thesis['facts']] == ['fact:cash']
    assert [lead['source_id'] for lead in thesis['external_leads']] == ['web:11']
    assert result['investment_draft']['points'] == draft_points
    assert result['investment_analysis']['stance'] == 'cautiously_bullish'
    assert result['investment_review']['opinion'] == 'approved'
    assert result['investment_review']['review'] == ('review_partial' if risk_review_fails else 'review_completed')
    sections = result['investment_narrative']['sections']
    assert sections[0]['evidence_ids'] == ['sec:verified', 'web:11']
    assert sections[1]['review_verdict'] == 'unverifiable' and sections[1]['review_reason']
    assert sections[2]['text'] == draft_points[2]['text']
    assert any('Some draft citations could not be located' in note for note in result['limitations'])

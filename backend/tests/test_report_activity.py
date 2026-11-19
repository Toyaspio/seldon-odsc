from io import BytesIO
import json
from unittest.mock import Mock

import pytest

from backend.agent.limits import ResearchLimits
from backend.agent.pipeline import ToolRouter
from backend.providers.live import NebiusModelProvider, TavilySearchProvider
from backend.providers.sec import SecFinancialDataProvider, SecProviderError


def test_search_events_record_response_and_only_final_citations(monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY', 'fixture-value')
    limits = ResearchLimits()
    limits.providers['tavily'].enabled = True
    answer = dict(results=[{'url': 'https://example.com/used'}, {'url': 'https://example.com/unused'},
                           {'url': 'http://example.com/unusable'}])
    provider = TavilySearchProvider(opener=Mock(side_effect=lambda *_args, **_kw:
                                               BytesIO(json.dumps(answer).encode())))
    router = ToolRouter(provider, None)
    router.search_round(['First fixture-value'], limits, stages={'First fixture-value': 'initial'},
                        topics={'First fixture-value': 'Check outlook'})
    router.search_round(['Second'], limits, stages={'Second': 'counterevidence'})
    limits.attribute_search_citations({'https://example.com/used': 'source-id'})
    first, second = limits.events
    assert first.query == 'First [redacted]' and first.topic == 'Check outlook'
    assert first.stage == 'initial' and second.stage == 'counterevidence'
    assert (second.http_attempts, second.http_responses, second.result_count, second.usable_results) == (1, 1, 3, 2)
    assert first.adopted_source_ids == []
    assert second.adopted_source_ids == ['source-id']


def test_sec_successful_http_with_invalid_document_is_not_a_parse_success(monkeypatch):
    monkeypatch.setattr('backend.providers.sec.time.sleep', lambda _: None)
    limits = ResearchLimits()
    limits.providers['sec'].enabled = True
    provider = SecFinancialDataProvider(user_agent='Fixture test@example.org',
                                       opener=Mock(return_value=BytesIO(b'{"wrong":{}}')))
    with pytest.raises(SecProviderError):
        with limits.provider_call('sec', stage='company_facts', company_id='msft'):
            provider.facts('789019')
    event, = limits.events
    assert (event.http_attempts, event.http_responses, event.outcome) == (1, 1, 'failed')
    assert event.parsed is False and event.extracted is None and event.verified is None


@pytest.mark.parametrize(('issuer','stance','support','opposition'), [
    ('Alpha', 'cautiously_bullish', 'Cash conversion improved with sales growth.',
     'Rising capacity costs could weaken returns.'),
    ('Beta', 'cautiously_bearish', 'Renewals are still stable.',
     'Consensus assumes a margin rebound despite cash deterioration.'),
])
def test_synthesis_contract_accepts_distinct_evidence_weighted_views(issuer, stance, support, opposition):
    topics = [
        ('thesis', f'{issuer}: weigh cash conversion more than headline growth.'),
        ('supporting_case', support), ('opposing_case', opposition),
        ('market_expectations', 'Compare normalized cash performance with expectations.'),
        ('bull_case', 'If utilization rises and capex moderates, cash conversion can improve.'),
        ('base_case', 'At steady utilization, watch operating cash flow and capacity cost.'),
        ('bear_case', 'If utilization falls, earnings and valuation could compress.'),
    ]
    payload = {'stance': stance, 'confidence': 'low', 'missing_inputs': [],
               'points': [dict(topic=topic, text=text, evidence_ids=['source']) for topic, text in topics]}
    response = {'choices':[{'message':{'content':json.dumps(payload)}}],
                'usage': {'prompt_tokens': 101, 'completion_tokens': 82}}
    provider = NebiusModelProvider(api_key='fixture', opener=Mock(return_value=BytesIO(json.dumps(response).encode())))
    limits = ResearchLimits()
    limits.providers['nebius'].enabled = True
    with limits.provider_call('nebius', stage='investment_synthesis'):
        analysis = provider.synthesize_investment({'question': issuer}, limits)
    assert analysis.stance == stance and len(analysis.points) == len(topics)
    event, = limits.events
    assert (event.stage, event.prompt_tokens, event.completion_tokens) == ('investment_synthesis', 101, 82)

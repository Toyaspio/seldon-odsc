import json
from io import BytesIO
import pytest
from urllib.error import HTTPError
from backend.providers.sec import SecCompanyResolver, SecFinancialDataProvider, SecProviderError
from backend.agent.pipeline import (ResearchOrchestrator, ResearchPlanner, CompanyResolver,
    ToolRouter, EvidenceExtractor, FinancialValidator, EvidenceVerifier, ReportBuilder)
from backend.providers.interfaces import MockModelProvider, MockSearchProvider, MockFinancialDataProvider


class Response(BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode())


def test_company_resolution_and_ambiguity_guard():
    assert SecCompanyResolver().resolve('MSFT')['cik'] == '0000789019'
    with pytest.raises(SecProviderError):
        SecCompanyResolver().resolve('Unknown international company')


def test_sec_facts_keep_filed_period_and_respect_as_of():
    payload = {'facts': {'us-gaap': {'PaymentsToAcquirePropertyPlantAndEquipment': {
        'units': {'USD': [
            {'val': 100, 'start': '2025-01-01', 'end': '2025-03-31', 'filed': '2025-04-20', 'form': '10-Q', 'fy': 2025, 'fp': 'Q1', 'accn': 'a1'},
            {'val': 300, 'start': '2025-01-01', 'end': '2025-06-30', 'filed': '2025-07-20', 'form': '10-Q', 'fy': 2025, 'fp': 'Q2', 'accn': 'a2'},
            {'val': 500, 'start': '2025-01-01', 'end': '2025-09-30', 'filed': '2025-10-20', 'form': '10-Q', 'fy': 2025, 'fp': 'Q3', 'accn': 'a3'},
        ]}}}}}
    provider = SecFinancialDataProvider(user_agent='Seldon test contact@example.org',
                                       opener=lambda request, timeout: Response(payload))
    rows = provider.facts('Microsoft', '2025-06-30')
    assert len(rows) == 1  # Q2 ended by the cutoff but had not been filed.
    rows = provider.facts('Microsoft', '2025-07-20')
    assert len(rows) == 2
    assert rows[-1]['value'] == 300
    assert rows[-1]['period_start'] == '2025-01-01'
    assert rows[-1]['fiscal_period'] == 'Q2'


def test_sec_requires_identifying_user_agent(monkeypatch):
    monkeypatch.delenv('SEC_USER_AGENT', raising=False)
    provider = SecFinancialDataProvider(user_agent=None)
    with pytest.raises(SecProviderError, match='SEC_USER_AGENT'):
        provider.facts('Microsoft')


def test_sec_retries_transient_error_without_leaking_contact(monkeypatch):
    calls = []
    def flaky(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 429, 'rate limited', {}, None)
        return Response({'ok': True})
    monkeypatch.setattr('backend.providers.sec.time.sleep', lambda seconds: None)
    provider = SecFinancialDataProvider(user_agent='Seldon Research Agent contact@example.org',
                                        opener=flaky)
    assert provider._get_json('https://data.sec.gov/example.json') == {'ok': True}
    assert len(calls) == 2


def test_sec_cached_response_avoids_request():
    from backend.providers.sec import urlopen
    provider = SecFinancialDataProvider(user_agent='Seldon Research Agent contact@example.org',
                                        opener=urlopen)
    url = 'https://data.sec.gov/cache-test.json'
    import time
    type(provider)._facts_cache[url] = (time.monotonic(), {'cached': True})
    try:
        assert provider._get_json(url) == {'cached': True}
    finally:
        type(provider)._facts_cache.pop(url, None)


def test_sec_requests_share_one_rate_gate(monkeypatch):
    waits = []
    monkeypatch.setattr('backend.providers.sec.time.sleep', waits.append)
    provider = SecFinancialDataProvider(user_agent='Seldon Research Agent contact@example.org',
                                        opener=lambda request, timeout: Response({'ok': True}))
    provider._get_json('https://data.sec.gov/first.json')
    provider._get_json('https://data.sec.gov/second.json')
    assert any(wait >= 0.1 for wait in waits)


def test_sec_report_keeps_data_source_and_period_provenance():
    class FixtureFacts:
        def facts(self, company_id, as_of_date=None):
            return [{'cik': '0000789019', 'tag': 'PaymentsToAcquirePropertyPlantAndEquipment',
                'taxonomy': 'us-gaap', 'unit': 'USD', 'value': 42, 'period_start': '2025-01-01',
                'period_end': '2025-03-31', 'filed_at': '2025-04-20', 'form': '10-Q',
                'fy': 2025, 'fp': 'Q1', 'accession_number': 'abc',
                'source_url': 'https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json',
                'metric_name': 'cash_capex', 'company_id': 'msft'}]
    orchestrator = ResearchOrchestrator(ResearchPlanner(MockModelProvider()), CompanyResolver(),
        ToolRouter(MockSearchProvider(), MockFinancialDataProvider()), EvidenceExtractor(),
        FinancialValidator(), EvidenceVerifier(), ReportBuilder())
    report = orchestrator.run_sec('Microsoft CapEx', ['Microsoft'], data_provider=FixtureFacts())
    assert report.status == 'partial'
    assert report.data_mode == 'unverified'
    assert report.companies[0].cik == '0000789019'
    assert report.metrics[0].period_start == '2025-01-01'
    assert report.metrics[0].evidence_ids == [report.sources[0].source_id]
    assert report.claims == []


def test_live_agent_quarantines_conflicting_disclosures():
    from backend.providers.interfaces import ModelProvider
    class ClaimModel(ModelProvider):
        def plan(self, query):
            return {'companies': ['Microsoft'], 'core_question': 'Cash capex',
                    'metrics': ['cash_capex'], 'preferred_source_types': ['filing'],
                    'retrieval_tasks': [{'query': 'Microsoft cash capex', 'purpose': 'Find disclosures'}],
                    'verification_questions': ['Are the figures consistent?'], 'comparison': 'latest'}
    class FixtureFacts:
        def facts(self, company_id, as_of_date=None):
            fact = {'cik': '0000789019', 'tag': 'PaymentsToAcquirePropertyPlantAndEquipment',
                'taxonomy': 'us-gaap', 'unit': 'USD', 'value': 42, 'period_start': '2025-01-01',
                'period_end': '2025-03-31', 'filed_at': '2025-04-20', 'form': '10-Q',
                'fy': 2025, 'fp': 'Q1', 'accession_number': 'abc',
                'source_url': 'https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json',
                'metric_name': 'cash_capex', 'company_id': 'msft'}
            return [fact, dict(fact, value=99, accession_number='def')]
    pipeline = ResearchOrchestrator(ResearchPlanner(ClaimModel()), CompanyResolver(),
        ToolRouter(MockSearchProvider(), MockFinancialDataProvider()), EvidenceExtractor(),
        FinancialValidator(), EvidenceVerifier(), ReportBuilder())
    report = pipeline.run_agent('Microsoft CapEx', ['Microsoft'], data_provider=FixtureFacts())
    assert len(report.claims) == 3
    assert [claim.verification_status for claim in report.claims].count('contradicted') == 2
    assert any(claim.verification_status == 'insufficient' for claim in report.claims)
    assert not report.metrics
    assert report.status == 'partial'


def test_company_facts_keeps_valid_observations_beside_damaged_rows():
    good = {'val': 100, 'start': '2025-01-01', 'end': '2025-03-31',
            'filed': '2025-05-01', 'form': '10-Q', 'accn': '0001826011-25-001000'}
    payload = {'facts': {'us-gaap': {
        'RevenueFromContractWithCustomerExcludingAssessedTax': {'units': {'USD': [None, good], 'EUR': False}},
        'NetCashProvidedByUsedInOperatingActivities': {'units': None},
    }}}
    provider = SecFinancialDataProvider(user_agent='Seldon fixture contact@example.org',
                                       opener=lambda *_args, **_kwargs: Response(payload))
    rows = provider.facts('0001826011', '2025-06-01')
    assert [(row['metric_name'], row['value']) for row in rows] == [('revenue', 100)]

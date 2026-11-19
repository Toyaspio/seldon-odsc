import pytest
from backend.providers.interfaces import MockModelProvider, MockSearchProvider, MockFinancialDataProvider, NebiusModelProvider
from backend.providers.live import ProviderError
from backend.agent.pipeline import ResearchOrchestrator, ResearchPlanner, CompanyResolver, ToolRouter, EvidenceExtractor, FinancialValidator, EvidenceVerifier, ReportBuilder


def test_mock_pipeline_and_live_guard():
    pipeline = ResearchOrchestrator(ResearchPlanner(MockModelProvider()), CompanyResolver(),
        ToolRouter(MockSearchProvider(), MockFinancialDataProvider()), EvidenceExtractor(),
        FinancialValidator(), EvidenceVerifier(), ReportBuilder())
    result = pipeline.run_mock('Research Oracle', ['Oracle'])
    assert result['status'] == 'partial'
    assert result['companies'][0]['company_name'] == 'Oracle'
    assert result['sources'] == []
    with pytest.raises(ProviderError, match='NEBIUS_API_KEY'):
        NebiusModelProvider().plan('test')

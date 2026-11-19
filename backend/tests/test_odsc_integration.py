from backend.agent.models import Source
from backend.agent.pipeline import selected_web_claims


def test_selection_rejects_invented_quotes_and_source_ids():
    quote = 'The issuer identified capacity constraints as a risk.'
    source = Source(source_id='web:filing', title='Filing', publisher='www.sec.gov',
                    url='https://www.sec.gov/filing', source_type='regulator_lead',
                    quoted_text=quote)
    valid = {'source_id': source.source_id, 'quote': quote, 'topic': 'risk'}
    choices = [dict(valid, source_id='invented'), dict(valid, quote='The issuer overcame all constraints.'),
               dict(valid, topic='verified'), valid, valid]
    claims = selected_web_claims(choices, {source.source_id: source})
    assert len(claims) == 1
    assert claims[0].evidence_ids == [source.source_id]
    assert claims[0].claim_type == 'analysis'
    assert claims[0].verification_status == 'insufficient'


def test_startup_preserves_backend_environment(monkeypatch, tmp_path):
    from backend import settings

    settings_file = tmp_path / 'settings.env'
    settings_file.write_text('NEBIUS_MODEL=saved-model\nSEC_CONTACT_EMAIL=saved@example.test\n')
    monkeypatch.setattr(settings, 'SETTINGS_PATH', settings_file)
    monkeypatch.setenv('NEBIUS_MODEL', 'runtime-model')
    monkeypatch.setenv('NEBIUS_API_KEY', 'synthetic-key')
    monkeypatch.setenv('SEC_CONTACT_EMAIL', 'runtime@example.test')
    monkeypatch.delenv('SEC_USER_AGENT', raising=False)
    settings.load_saved_settings()
    import os
    assert os.environ['NEBIUS_MODEL'] == 'runtime-model'
    assert os.environ['NEBIUS_API_KEY'] == 'synthetic-key'
    assert os.environ['SEC_USER_AGENT'].endswith('runtime@example.test')


def test_amazon_short_name_resolves_against_edgar_directory():
    from backend.providers.sec import SecCompanyResolver

    directory = [{'name': 'AMAZON COM INC', 'ticker': 'AMZN', 'cik': '0001018724'}]
    assert SecCompanyResolver().matches('Amazon', directory) == directory


def test_tavily_quota_stops_search_round_without_retry():
    from urllib.error import HTTPError

    from backend.agent.limits import ResearchLimits
    from backend.agent.pipeline import ToolRouter
    from backend.providers.interfaces import MockFinancialDataProvider
    from backend.providers.live import TavilySearchProvider

    attempts = []

    def exhausted(request, timeout):
        attempts.append(request.full_url)
        raise HTTPError(request.full_url, 432, 'synthetic quota failure', {}, None)

    router = ToolRouter(TavilySearchProvider(api_key='fixture', opener=exhausted),
                        MockFinancialDataProvider())
    issues = []
    assert router.search_round(['first', 'another'], ResearchLimits(), issues) == []
    assert len(attempts) == 1
    assert issues == ['Tavily usage limit reached. Search evidence may be incomplete.']


def test_amazon_cash_capex_tag_uses_annual_filings_for_full_year():
    from backend.providers.sec import SecFinancialDataProvider

    annual = dict(form='10-K', val=131819000000, filed='2026-02-06',
                  start='2025-01-01', end='2025-12-31', accn='0001018724-26-000004')
    trailing = dict(form='10-Q', val=140000000000, filed='2026-05-01',
                    start='2025-04-01', end='2026-03-31')
    company_facts = {'facts': {'us-gaap': {
        'PaymentsToAcquireProductiveAssets': {'units': {'USD': [annual, trailing]}}
    }}}

    class FixtureSec(SecFinancialDataProvider):
        def _get_json(self, url, timeout_seconds=None):
            return company_facts

    provider = FixtureSec()
    rows = provider.facts('1018724', '2026-11-18')
    assert [(row['metric_name'], row['value']) for row in rows] == [('cash_capex', annual['val'])]
    assert rows[0]['capex_scope'] == 'ppe'
    other = provider.facts('789019', '2026-11-18')
    assert len(other) == 1 and other[0]['capex_scope'] == 'productive_assets'

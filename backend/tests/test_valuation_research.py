from datetime import date
from decimal import Decimal
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.agent.limits import ResearchLimits
from backend.agent.models import Claim, Company, InvestmentAnalysis, Metric
from backend.agent.pipeline import ToolRouter, scoped_research_quality
from backend.financial.valuation import calculate_valuation, fetch_close, run_valuation
from backend.providers.live import NebiusModelProvider, TavilySearchProvider
from test_capex_coverage import FACTS, audited_report


def issuer(ticker='NVDA'):
    return Company(company_id=ticker.lower(), company_name=ticker, ticker=ticker,
                   resolution_status='resolved')


@pytest.mark.parametrize('ticker', sorted(FACTS))
def test_ten_frozen_issuers_never_infer_market_prices_from_sec(ticker):
    metrics, sources, _ = audited_report(ticker)
    company = issuer(ticker)
    record, market_sources = run_valuation([company], metrics, sources, set(), {company.company_id},
                                          ResearchLimits(), date(2026, 11, 19))
    assert len(FACTS) == 10
    assert record[0].status == 'data_insufficient' and not record[0].measures
    assert not market_sources
    assert ('bank' in record[0].qualitative) == (ticker == 'JPM')


def test_deterministic_ratios_require_compatible_verified_inputs():
    day = date(2026, 4, 17)
    anchor = '2026-03-31'

    def metric(name, value, start='2025-04-01', end=anchor, unit='USD', currency='USD', scope=None):
        return Metric(metric_id=name, company_id='nvda', metric_name=name, value=value, currency=currency,
                      unit=unit, period_start=start, period_end=end, fiscal_period='FY', accounting_basis='US GAAP',
                      actual_or_guidance='actual', evidence_ids=[name], capex_scope=scope)

    rows = [
        metric('revenue', 500), metric('net_income_common', 100),
        metric('free_cash_flow', 80, scope='productive_assets'),
        metric('common_shares_outstanding', 20, None, day.isoformat(), 'shares', None),
        metric('total_debt_and_finance_leases', 40, None), metric('cash_and_equivalents', 20, None),
        metric('common_equity', 200, None),
    ]
    sources = {m.metric_id: SimpleNamespace(publication_date=day.isoformat(), source_type='sec_xbrl') for m in rows}
    measures, missing = calculate_valuation(issuer(), rows, sources, day, Decimal('20'), 'market')
    by_name = {m.name: m for m in measures}
    assert by_name['P/E'].value == 4
    assert by_name['P/S'].value == .8
    assert by_name['P/B'].value == 2
    assert by_name['EV/FCF'].value == 5.25
    assert by_name['FCF yield'].value == 20
    assert 'market' in by_name['EV/FCF'].evidence_ids
    assert not {'Forward P/E', 'DCF', 'EV/EBITDA'} & by_name.keys()
    assert any('consensus' in item for item in missing)
    rows[-2] = rows[-2].model_copy(update={'unit':'TWD', 'currency':'TWD'})
    measures, _ = calculate_valuation(issuer(), rows, sources, day, Decimal(20), 'market')
    assert not any(m.name == 'EV/FCF' for m in measures)
    sources['revenue'].publication_date = '2026-04-18'
    measures, _ = calculate_valuation(issuer(), rows, sources, day, Decimal(20), 'market')
    assert not any(m.name == 'P/S' for m in measures)
    rows[3] = rows[3].model_copy(update={'period_end':'2026-04-16'})
    measures, _ = calculate_valuation(issuer(), rows, sources, day, Decimal(20), 'market')
    assert not any(m.name == 'Market capitalization' for m in measures)


def test_nasdaq_reopens_source_instead_of_using_search_snippet(monkeypatch):
    # Minimal projection of the publicly retrieved Nasdaq NVDA row from this review.
    payload = {'data': {'symbol':'NVDA', 'tradesTable': {'rows':[{'date':'11/17/2026','close':'$155.46'}]}}}
    public = Mock(side_effect=lambda *args, **kwargs: BytesIO(json.dumps(payload).encode()))
    from backend.financial.valuation import _read_market_document
    monkeypatch.setattr('backend.financial.valuation._read_market_document',
                        lambda request, timeout, on_response, opener:
                            _read_market_document(request, timeout, on_response, public))
    search_payload = {'results': [{'url':'https://www.nasdaq.com/market-activity/stocks/nvda/historical',
                                  'content':'untrusted price $1,000'}]}
    search_opener = Mock(return_value=BytesIO(json.dumps(search_payload).encode()))
    limits = ResearchLimits()
    limits.providers['tavily'].enabled = True
    router = ToolRouter(TavilySearchProvider(api_key='fixture', opener=search_opener), None)
    response = router.search_round(['NVDA closing quote'], limits, stages={'NVDA closing quote':'valuation_market'})
    result, sources = run_valuation([issuer()], [], [], {item['url'] for item in response}, {'nvda'}, limits,
                                    date(2026, 11, 19))
    assert search_opener.called and limits.events[0].http_attempts == 1
    assert public.call_args.args[0].full_url.startswith('https://api.nasdaq.com/api/quote/NVDA/historical?')
    assert public.call_args.args[0].get_header('Authorization') is None
    assert result[0].price == 155.46 and result[0].trade_date == '2026-11-17'
    assert result[0].status == 'partial' and sources[0].source_id == result[0].price_source_id
    assert limits.events[-1].http_responses == 1
    result, _ = run_valuation([issuer()], [], [], set(), set(), limits, date(2026, 11, 19), historical=True)
    assert result[0].status == 'not_executed' and public.call_count == 1


def test_quote_rejects_other_ticker_and_outdated_rows():
    payload = {'data': {'symbol':'NVDA', 'tradesTable': {'rows':[{'date':'11/01/2026','close':'$155.46'}]}}}
    opener = lambda *args: BytesIO(json.dumps(payload).encode())
    with pytest.raises(ValueError, match='No recent'):
        fetch_close('NVDA', date(2026, 11, 19), 5, opener=opener)
    with pytest.raises(ValueError, match='symbol mismatch'):
        fetch_close('AMZN', date(2026, 11, 19), 5, opener=opener)


def test_only_core_gaps_control_completion_and_old_conflicts_are_deduplicated():
    company = issuer()
    metric = SimpleNamespace(company_id='nvda', metric_name='revenue', actual_or_guidance='actual',
                             period_end='2026-10-25')
    conflict = Claim(claim_id='conflict', company_id='nvda', text='Conflict', metric_name='revenue',
                     verification_status='contradicted', claim_type='evidence_gap', evidence_ids=[],
                     period_start='2014-01-01', period_end='2014-03-31')
    quality = scoped_research_quality('Review latest revenue', [company], [metric], [conflict, conflict],
                                      [(company, 'ai_roi')], [])
    assert not quality.core_gaps and quality.historical_conflicts == 1 and quality.supplemental_gaps
    current = conflict.model_copy(update={'period_end':'2026-10-25'})
    assert scoped_research_quality('Review revenue', [company], [metric], [current], [], []).core_gaps
    assert scoped_research_quality('Review 2014 revenue', [company], [metric], [conflict], [], []).core_gaps
    assert scoped_research_quality('Review Forward P/E', [company], [metric], [], [], []).core_gaps
    assert scoped_research_quality('Review latest results', [company], [metric], [], [], []).core_gaps == []


def test_valuation_context_survives_unavailable_model_review(monkeypatch):
    from backend.agent.investment import analyze_investment
    from backend.agent.investment_review import run_review
    from backend.agent.models import IssuerValuation, ValuationMeasure
    from backend.providers.live import ProviderError

    measure = ValuationMeasure(name='P/E', value=20, unit='x', formula='cap ÷ earnings', inputs={},
                              evidence_ids=['official', 'quote'], basis='trailing_actual', period_end='2026-04-01')
    valuation = IssuerValuation(company_id='nvda', status='partial', price_source_id='quote', measures=[measure])
    actual = dict(company_id='nvda', metric_name='revenue', currency='USD', unit='USD',
                  period_end='2026-04-01', duration_kind='quarter', accounting_basis='US GAAP',
                  actual_or_guidance='actual', capex_scope=None, evidence_ids=['official'])
    metric = SimpleNamespace(**actual, model_dump=lambda **kw: actual)
    lead = SimpleNamespace(source_id='lead', publisher='Fixture', publication_date=None, company_id='nvda',
                           title='Outlook', quoted_text='Reported outlook')
    draft = InvestmentAnalysis(stance='neutral', confidence='medium', points=[
        {'topic':'valuation','text':'Valuation depends on normalized earnings.','evidence_ids':['quote','official']}],
        missing_inputs=[])
    model = NebiusModelProvider()
    synth = Mock(return_value=draft)
    monkeypatch.setattr(NebiusModelProvider, 'synthesize_investment', synth)
    review = Mock(side_effect=ProviderError('fixture'))
    monkeypatch.setattr(NebiusModelProvider, 'review_batch', review)
    jobs = []
    limits = ResearchLimits()
    limits.providers['nebius'].enabled = True
    plan = SimpleNamespace(core_question='Valuation?', verification_questions=[])
    analyze_investment('Valuation', plan, [metric], [SimpleNamespace(source_id='official')],
                       [lead], model, limits, Mock(), [], False, on_draft=jobs.append, valuations=[valuation])
    assert synth.call_args.args[0]['valuation_research'][0]['measures'][0]['value'] == 20
    _, state = run_review(jobs[0], model, limits, lambda: None)
    assert state.opinion == 'provisional'
    assert jobs[0]['draft']['points'][0]['topic'] == 'valuation'
    assert review.call_args.args[0]['valuation_research'][0]['measures'][0]['name'] == 'P/E'


def test_correct_verdict_survives_overlong_explanation():
    from backend.agent.investment_review import _decode
    reason = 'Primary evidence supports scope and units. ' * 10
    decoded = _decode(json.dumps({'verdicts':[{'item_id':'0','verdict':'supported','short_reason':reason}]}), ['0'])
    assert decoded['0'][0] == 'supported'
    assert decoded['0'][1].endswith('…')
    with pytest.raises(ValueError):
        _decode(json.dumps({'verdicts':[{'item_id':'9','verdict':'supported','short_reason':reason}]}), ['0'])


def test_narrow_price_question_can_complete_without_forecast_inputs(monkeypatch):
    monkeypatch.setattr('backend.financial.valuation.fetch_close',
                        lambda *args, **kwargs: (Decimal('155.46'), date(2026, 11, 17),
                                                  'https://api.nasdaq.com/api/quote/NVDA/historical'))
    company = issuer()
    leads = {'https://www.nasdaq.com/market-activity/stocks/nvda/historical'}
    records, _ = run_valuation([company], [], [], leads, {'nvda'}, ResearchLimits(),
                               date(2026, 11, 19), query='What is the latest share price?')
    assert records[0].status == 'completed'
    assert not records[0].measures
    assert any('consensus' in gap for gap in records[0].missing)

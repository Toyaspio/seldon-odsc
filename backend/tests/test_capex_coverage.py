import json
from pathlib import Path

import pytest

from backend.agent.models import Claim, Company, Metric
from backend.evidence.extractor import EvidenceExtractor
from backend.evidence.verifier import EvidenceVerifier
from backend.financial.validator import FinancialValidator, period_kind
from backend.providers.sec import SecFinancialDataProvider
from backend.providers.sec_capex_filings import FILINGS, parse_reviewed_filing


FIXTURES = Path(__file__).parent / 'fixtures' / 'sec_capex'
FACTS = json.loads((FIXTURES / 'companyfacts.json').read_bytes())
ARCHIVES = {filing.url: FIXTURES / f'{filing.accession}.html' for filing in FILINGS}


def audited_report(symbol, cutoff='2026-11-19'):
    snapshot = FACTS[symbol]
    company = Company(company_id=symbol.lower(), company_name=snapshot['entityName'],
                      cik=f"{snapshot['cik']:010}", ticker=symbol, resolution_status='resolved')
    provider = SecFinancialDataProvider()
    provider._get_json = lambda *_: snapshot
    provider._get_filing = lambda url, _: ARCHIVES[url].read_bytes()
    rows = provider.facts(company.cik, cutoff)
    facts, sources = EvidenceExtractor().structured(company, rows)
    audited, claims = EvidenceVerifier().audit_facts(facts, sources, {company.company_id: company.company_name})
    return audited + FinancialValidator().calculate(audited, 'annual', claims=claims), sources, claims


@pytest.mark.parametrize(('symbol', 'unit', 'end', 'duration', 'scope', 'capex', 'fcf'), [
    ('MSFT', 'USD', '2026-09-30', 'quarter', 'ppe', 39367000000, 18633000000),
    ('GOOGL', 'USD', '2026-09-30', 'ytd', 'ppe', 131953000000, -4804000000),
    ('AMZN', 'USD', '2026-09-30', 'quarter', 'ppe', 77259000000, -34837000000),
    ('META', 'USD', '2026-09-30', 'ytd', 'ppe', 83055000000, 13818000000),
    ('NVDA', 'USD', '2026-10-25', 'quarter', 'productive_assets', 4422000000, 43831000000),
    ('TSM', 'TWD', '2026-09-30', 'ytd', 'ppe', 1289970777000, 1112145948000),
    ('TM', 'JPY', '2026-09-30', 'ytd', 'ppe_excluding_leased_equipment', 899729000000, 704195000000),
    ('BABA', 'CNY', '2026-06-30', 'quarter', 'ppe_excluding_campus_land', 67660000000, -44715000000),
    ('SHEL', 'USD', '2026-09-30', 'quarter', 'other_long_term_investing_assets', 7578000000, 13465000000),
])
def test_official_capex_and_same_period_fcf(symbol, unit, end, duration, scope, capex, fcf):
    metrics, sources, _ = audited_report(symbol)
    values = {metric.metric_name: metric for metric in metrics
              if metric.period_end == end and metric.currency == unit
              and period_kind(metric) == duration and metric.capex_scope == scope}
    assert values['cash_capex'].value == capex
    assert values['free_cash_flow'].value == fcf
    assert values['cash_capex'].period_start == values['free_cash_flow'].period_start
    assert values['free_cash_flow'].calculation_method == 'operating_cash_flow_less_capex'
    by_id = {source.source_id: source for source in sources}
    assert all(by_id[item].url.startswith('https://www.sec.gov/') for item in values['free_cash_flow'].evidence_ids)
    for source_id in values['cash_capex'].evidence_ids:
        source = by_id[source_id]
        assert source.financial_concept == 'Cash Capital Expenditures'
        assert source.original_numeric_value and source.tag_definition


def test_bank_and_unpaid_capex_are_not_interpreted_as_cash_capex():
    metrics, _, _ = audited_report('JPM')
    assert any(metric.metric_name == 'operating_cash_flow' for metric in metrics)
    assert not any(metric.metric_name in {'cash_capex', 'free_cash_flow'} for metric in metrics)
    _, sources, _ = audited_report('META')
    assert all(source.xbrl_tag != 'us-gaap:CapitalExpendituresIncurredButNotYetPaid' for source in sources)


def test_nvidia_filing_cutoff_and_scope_change_are_retained():
    latest, sources, _ = audited_report('NVDA')
    prior, prior_sources, _ = audited_report('NVDA', '2026-11-16')
    assert any(m.capex_scope == 'ppe' and m.period_end == '2020-07-26' for m in latest)
    assert {m.period_end for m in latest if m.capex_scope == 'productive_assets'
            and m.metric_name == 'cash_capex' and period_kind(m) == 'year'} >= {
                '2021-01-31', '2022-01-30', '2023-01-29', '2024-01-28', '2025-01-26', '2026-01-25',
            }
    assert any(s.xbrl_tag == 'nvda:PurchasesOfPropertyAndEquipmentAndIntangibleAssets'
               and s.period_end == '2021-01-31' for s in sources)
    assert any(m.capex_scope == 'productive_assets' and m.period_end == '2026-01-25'
               and m.metric_name == 'free_cash_flow' for m in latest)
    assert max(m.period_end for m in prior if m.metric_name == 'cash_capex') == '2026-07-26'
    assert any(s.source_type == 'sec_statement' and s.xbrl_tag is None for s in sources)
    assert not any(s.source_type == 'sec_statement' for s in prior_sources)


@pytest.mark.parametrize(('layout', 'unit', 'end', 'amount'), [
    ('nvda', 'USD', '2026-10-25', 115844000000),
    ('toyota', 'JPY', '2026-09-30', 26962462000000),
    ('alibaba', 'CNY', '2026-06-30', 268953000000),
    ('shell', 'USD', '2026-09-30', 92848000000),
])
def test_reviewed_income_statements_fill_revenue_even_when_capex_exists(layout, unit, end, amount):
    from datetime import date
    from time import monotonic
    from backend.providers.sec_capex_filings import reviewed_rows

    filing = next(item for item in FILINGS if item.layout == layout)
    provider = SecFinancialDataProvider()
    provider._get_filing = lambda url, _: ARCHIVES[url].read_bytes()
    existing = [{'metric_name': 'cash_capex', 'period_end': item.end, 'duration_kind': 'year'}
                for item in FILINGS if item.cik == filing.cik]
    rows = reviewed_rows(provider, filing.cik, date.fromisoformat(filing.filed), monotonic() + 10, existing)
    assert not any(row['metric_name'] == 'cash_capex' for row in rows)
    revenue = next(row for row in rows if row['metric_name'] == 'revenue' and row['period_end'] == end)
    assert (revenue['value'], revenue['unit'], revenue['source_url']) == (amount, unit, filing.url)
    assert all(row['unit'] == unit for row in rows if row['metric_name'] == 'revenue')
    assert not reviewed_rows(provider, filing.cik, date.fromisoformat(filing.filed), monotonic() + 10,
                             [*existing, revenue])


@pytest.mark.parametrize(('symbol', 'scope', 'end', 'tag'), [
    ('TM', 'ppe_excluding_leased_equipment', '2026-03-31',
     'tm:PurchaseOfPropertyPlantAndEquipmentExcludingEquipmentLeasedToOthersClassifiedAsInvestingActivities'),
    ('BABA', 'ppe_and_land_use', '2026-03-31', 'baba:PaymentsToAcquireLandUseRightsPropertyAndEquipment'),
    ('SHEL', 'other_long_term_investing_assets', '2025-12-31', 'ifrs-full:PurchaseOfOtherLongtermAssetsClassifiedAsInvestingActivities'),
    ('TSM', 'ppe', '2025-12-31', 'ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'),
])
def test_reviewed_inline_scope_retains_exact_tag(symbol, scope, end, tag):
    metrics, sources, _ = audited_report(symbol)
    assert any(m.metric_name == 'free_cash_flow' and m.period_end == end and period_kind(m) == 'year'
               and m.capex_scope == scope for m in metrics)
    assert any(s.period_end == end and s.xbrl_tag == tag and s.capex_scope == scope for s in sources)


def metric(name, value, start, end, scope=None, unit='USD', currency='USD'):
    return Metric(metric_id=f'{name}:{start}:{end}:{scope}', company_id='example', metric_name=name,
                  value=value, currency=currency, unit=unit, period_start=start, period_end=end,
                  fiscal_period=f'{start} to {end}', accounting_basis='US GAAP',
                  actual_or_guidance='actual', evidence_ids=['fixture'], capex_scope=scope)


@pytest.mark.parametrize(('other_scope', 'unit', 'currency', 'derived'), [
    ('ppe', 'USD', 'USD', True),
    ('productive_assets', 'USD', 'USD', False),
    ('ppe', 'millions', 'USD', False),
    ('ppe', 'EUR', 'EUR', False),
])
def test_ytd_difference_respects_scope_unit_and_currency(other_scope, unit, currency, derived):
    first = metric('cash_capex', 10, '2025-01-01', '2025-03-31', 'ppe')
    next_ = metric('cash_capex', 25, '2025-01-01', '2025-06-30', other_scope, unit, currency)
    result = FinancialValidator().calculate([first, next_], 'latest')
    assert bool(result) == derived
    if result:
        assert result[0].value == 15 and result[0].calculation_method == 'quarter_difference'
        assert result[0].capex_scope == 'ppe' and result[0].period_start == '2025-04-01'


def test_scope_change_blocks_growth_and_missing_capex_blocks_fcf():
    current = metric('cash_capex', 25, '2025-01-01', '2025-12-31', 'productive_assets')
    previous = metric('cash_capex', 10, '2024-01-01', '2024-12-31', 'ppe')
    cash_flow = metric('operating_cash_flow', 100, '2026-01-01', '2026-03-31')
    assert FinancialValidator().calculate([current, previous, cash_flow], 'annual') == []


def test_equal_duplicate_tags_merge_but_restatement_conflict_stays_excluded():
    base = dict(cik='0000001234', taxonomy='us-gaap', metric_name='cash_capex',
                capex_scope='ppe', duration_kind='year', period_start='2025-01-01',
                period_end='2025-12-31', form='10-K', unit='USD', value=100,
                source_url='https://www.sec.gov/example', filed_at='2026-02-01')
    cases = [dict(base, tag='first', accession_number='abc'),
             dict(base, tag='equivalent', accession_number='def')]
    company = Company(company_id='example', company_name='Example', cik=base['cik'], resolution_status='resolved')
    extract, verify = EvidenceExtractor(), EvidenceVerifier()

    def audit(rows):
        metrics, sources = extract.structured(company, rows)
        return verify.audit_facts(metrics, sources, {'example': 'Example'})

    audited, _ = audit(cases)
    assert len(audited) == 1 and len(audited[0].evidence_ids) == 2
    audited, claims = audit([*cases, dict(base, tag='first', accession_number='restated',
                                         value=180, form='10-K/A', filed_at='2026-04-01')])
    assert audited == []
    assert {claim.verification_status for claim in claims} == {'contradicted'}


def test_filing_rejects_unreviewed_or_conflicting_statement_columns():
    filing = FILINGS[0]
    content = ARCHIVES[filing.url].read_text(encoding='utf-8')
    assert any(row['metric_name'] == 'cash_capex' for row in parse_reviewed_filing(content.encode(), filing))
    bad = content + content.replace('(4,422)', '(4,421)')
    assert not any(row['metric_name'] == 'cash_capex' for row in parse_reviewed_filing(bad.encode(), filing))
    wrong_namespace = next(f for f in FILINGS if f.layout == 'inline' and f.cik == '0001046179')
    fragment = ARCHIVES[wrong_namespace.url].read_bytes().replace(b'https://xbrl.ifrs.org/', b'https://untrusted.invalid/')
    assert parse_reviewed_filing(fragment, wrong_namespace) == []


def test_official_tsm_quarter_uses_two_same_basis_cumulative_filings():
    metrics, sources, _ = audited_report('TSM')
    quarter = [metric for metric in metrics if metric.period_start == '2026-07-01'
               and metric.period_end == '2026-09-30' and metric.currency == 'TWD']
    values = {metric.metric_name: metric for metric in quarter}
    assert values['cash_capex'].value == 443206031000
    assert values['free_cash_flow'].value == 476569452000
    assert values['cash_capex'].calculation_method == 'quarter_difference'
    assert values['cash_capex'].accounting_basis == 'Taiwan-IFRS'
    by_id = {source.source_id: source for source in sources}
    assert len({by_id[evidence].url for evidence in values['cash_capex'].evidence_ids}) == 2


def test_conflicted_quarter_is_not_recreated_by_subtracting_cumulative_facts():
    first = metric('cash_capex', 10, '2025-01-01', '2025-03-31', 'ppe')
    second = metric('cash_capex', 25, '2025-01-01', '2025-06-30', 'ppe')
    disputed = Claim(claim_id='fixture', text='Conflicting filings', company_id='example',
                     metric_name='cash_capex', verification_status='contradicted', evidence_ids=[],
                     unit='USD', accounting_basis='US GAAP', capex_scope='ppe',
                     period_start='2025-04-01', period_end='2025-06-30')
    assert FinancialValidator().calculate([first, second], 'latest', claims=[disputed]) == []

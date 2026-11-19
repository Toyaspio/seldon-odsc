from dataclasses import replace
import json
from pathlib import Path

import pytest

from backend.agent.investment import apply_disclosed_fiscal_labels, cash_conversion_context
from backend.agent.investment_review import prepare_review, run_review, summarize
from backend.agent.limits import ResearchLimits
from backend.agent.models import Company, InvestmentAnalysis, InvestmentPoint
from backend.evidence.extractor import EvidenceExtractor
from backend.evidence.verifier import EvidenceVerifier
from backend.financial.validator import FinancialValidator
from backend.providers.sec_capex_filings import FILINGS, parse_reviewed_filing, row_record


@pytest.mark.parametrize(('start', 'end', 'label'), [
    ('2026-11-01', '2027-01-30', 'FY2027 Q4'),
    ('2026-12-01', '2027-02-28', 'FY2028 Q1'),
])
def test_issuer_fiscal_label_is_separate_from_observation_dates(start, end, label):
    filing = replace(FILINGS[0], filed='2027-04-01', fiscal_quarters=((end, label),))
    issuer = Company(company_id='issuer', company_name='Issuer', cik=filing.cik, resolution_status='resolved')
    row = row_record(filing, 'revenue', 'Revenue', 12, 12, 'USD', start, end, 'quarter')
    facts, _ = EvidenceExtractor().structured(issuer, [row])
    fact, = facts
    assert (fact.fiscal_period, fact.period_start, fact.period_end) == (label, start, end)
    wrong = f'Q3 FY2026 ({start} to {end}) had revenue of USD 12.'
    draft = InvestmentAnalysis(stance='bullish', confidence='medium', missing_inputs=[],
                               points=[InvestmentPoint(topic='thesis', text=wrong, evidence_ids=fact.evidence_ids)])
    documented = [fact.model_dump()]
    after = apply_disclosed_fiscal_labels(draft, {'0': fact.evidence_ids}, documented)
    assert after.points[0].text == f'{label} ({start} to {end}) had revenue of USD 12.'
    assert apply_disclosed_fiscal_labels(draft, {}, documented).points[0].text == wrong


def nvidia_facts():
    filing = FILINGS[0]
    exhibit = Path(__file__).parent / 'fixtures' / 'sec_capex' / (filing.accession + '.html')
    issuer = Company(company_id='nvda', company_name='NVIDIA CORP', cik=filing.cik, resolution_status='resolved')
    metrics, sources = EvidenceExtractor().structured(issuer, parse_reviewed_filing(exhibit.read_bytes(), filing))
    verified, claims = EvidenceVerifier().audit_facts(metrics, sources, {'nvda': issuer.company_name})
    return verified + FinancialValidator().calculate(verified, 'none', claims=claims), sources


def test_ytd_and_cash_conversion_keep_distinct_scope_and_citations():
    metrics, sources = nvidia_facts()
    revenue = next(f for f in metrics if f.metric_name == 'revenue' and f.fiscal_period == 'FY2027 Q3 YTD')
    disclosure = next(source for source in sources if source.source_id in revenue.evidence_ids)
    assert (revenue.value, revenue.period_start, revenue.period_end) == (293_681_000_000, '2026-01-26', '2026-10-25')
    conflict = disclosure.model_copy(update={'source_id': 'conflicting', 'numeric_value': 290_000_000_000})
    verified, claims = EvidenceVerifier().audit_facts([revenue], sources + [conflict], {'nvda': 'NVIDIA CORP'})
    assert not verified
    assert claims[0].verification_status == 'contradicted'

    ratios = cash_conversion_context([fact.model_dump() for fact in metrics])
    quarter = next(item for item in ratios if item['fiscal_period'] == 'FY2027 Q3')
    assert quarter['free_cash_flow'] == 43_831_000_000
    assert quarter['operating_cash_flow'] == 48_253_000_000
    assert quarter['free_cash_flow_to_operating_cash_flow_pct'] == 90.84
    assert revenue.evidence_ids[0] not in quarter['evidence_ids']


def test_review_keeps_attribution_local_and_preserves_anchored_direction():
    metrics, _ = nvidia_facts()
    quarter, ytd = (next(fact for fact in metrics if fact.metric_name == 'revenue' and fact.fiscal_period == label)
                    for label in ('FY2027 Q3', 'FY2027 Q3 YTD'))
    points = [
        InvestmentPoint(topic='thesis', text='Quarterly revenue supports a cautiously bullish view. YTD needs its own citation.',
                        evidence_ids=quarter.evidence_ids),
        InvestmentPoint(topic='outlook', text='Nine-month revenue was $293.681 billion.', evidence_ids=ytd.evidence_ids),
        InvestmentPoint(topic='risk', text='The issuer announced a CEO transition.', evidence_ids=['issuer:notice']),
        InvestmentPoint(topic='base_case', text='If AMD wins more workloads, competitive pressure could rise.',
                        evidence_ids=['web:competition']),
    ]
    draft = InvestmentAnalysis(stance='cautiously_bullish', confidence='medium', missing_inputs=[], points=points)
    context = {'question': 'NVIDIA outlook', 'verified_financial_facts': [quarter.model_dump(), ytd.model_dump()],
               'unverified_external_leads': [{'source_id': 'issuer:notice', 'source_type': 'issuer_lead'},
                                             {'source_id': 'web:competition', 'source_type': 'web_search'}]}
    job = prepare_review(draft, context, {'nvda'}, {str(i): point.evidence_ids for i, point in enumerate(points)}, 'fixture')

    class Reviewer:
        def review_batch(self, payload, limits, cap):
            for item in payload['items']:
                allowed = {'0': [quarter.metric_id], '1': [ytd.metric_id], '2': [], '3': []}
                assert item['cited_financial_facts'] == allowed[item['item_id']]
                assert item['evidence_ids'] == points[int(item['item_id'])].evidence_ids
            return json.dumps({'verdicts': [
                {'item_id': item['item_id'],
                 'verdict': 'partially_supported' if item['item_id'] == '0' else 'supported',
                 'short_reason': 'The paragraph needs an independent YTD citation.'}
                for item in payload['items']]})

    limits = ResearchLimits()
    limits.providers['nebius'].enabled = True
    analysis, status = run_review(job, Reviewer(), limits, lambda: None)
    assert status.opinion == 'provisional' and status.verdicts['1'] == 'supported'
    assert analysis.stance == 'cautiously_bullish'
    assert analysis.points == points
    job['verdicts']['0'] = 'supported'
    assert summarize(job)[1].opinion == 'approved'



def test_exact_ytd_revenue_rebinds_to_the_cited_issuer_period():
    metrics, _ = nvidia_facts()
    facts = [metric.model_dump() for metric in metrics]
    expected = next(metric for metric in metrics
                    if metric.metric_name == 'revenue' and metric.fiscal_period == 'FY2027 Q3 YTD')
    cash = next(metric for metric in metrics
                if metric.metric_name == 'operating_cash_flow' and metric.fiscal_period == 'FY2027 Q3 YTD')
    thesis = InvestmentPoint(topic='thesis', text='YTD FY2026 revenue reached $293.681B.',
                             evidence_ids=cash.evidence_ids)
    draft = InvestmentAnalysis(stance='bullish', confidence='medium', missing_inputs=[], points=[thesis])
    citations = {'0': cash.evidence_ids}
    updated = apply_disclosed_fiscal_labels(draft, citations, facts)
    assert updated.points[0].text == 'FY2027 through Q3 revenue reached $293.681B.'
    assert citations['0'] == cash.evidence_ids + expected.evidence_ids
    citations = {'0': cash.evidence_ids}
    unverified = [fact for fact in facts if fact['metric_id'] != expected.metric_id]
    assert apply_disclosed_fiscal_labels(draft, citations, unverified) == draft
    assert citations['0'] == cash.evidence_ids

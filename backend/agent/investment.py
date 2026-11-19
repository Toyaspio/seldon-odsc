import re

from backend.evidence.registry import EvidenceRegistry
from backend.financial.validator import period_kind
from backend.financial.valuation import valuation_is_core
from backend.providers.live import NebiusModelProvider, ProviderError
from .limits import ResourceLimitReached
from .models import InvestmentAnalysis
from .investment_review import prepare_review, summarize, supported_direction


def investment_requested(query: str) -> bool:
    if valuation_is_core(query) or re.search(
            r"\b(bullish|bearish|valuation|investments?|investing|stocks?|upside|downside|buy(?:ing)?|sell(?:ing)?|attractive at|priced in)\b|投资|看多|看空|估值|股价|买入|卖出|加仓|减仓",
            query, re.IGNORECASE):
        return True
    disclosure = re.search(
        r"\b(earnings|financial (?:report|results|statements?)|quarterly results|annual report)\b|财报|财务报告|业绩",
        query, re.IGNORECASE)
    consequences = re.search(
        r"\b(impact|implications?|outlook|mean for|affect(?:s|ed)?|going forward)\b|影响|前景|意味着|后续走势",
        query, re.IGNORECASE)
    return bool(disclosure and consequences)


def analyze_investment(query, plan, metrics, sources, web_sources, model, limits, progress,
                       issues, historical_day, input_reports=(), issuer_ids=(), on_draft=None, valuations=(), news=None,
                       registry=None):
    if not isinstance(model, NebiusModelProvider):
        return None

    official_ids = {source.source_id for source in sources}
    recent = sorted(metrics, key=lambda metric: metric.period_end, reverse=True)
    by_concept = {}
    for metric in recent:
        if (metric.actual_or_guidance == 'actual' and metric.evidence_ids
                and set(metric.evidence_ids) <= official_ids):
            dimensions = (metric.company_id, metric.metric_name, period_kind(metric), metric.currency,
                          metric.unit, metric.accounting_basis, metric.capex_scope)
            by_concept.setdefault(dimensions, metric)
    # Give each issuer a slot before filling the remaining bounded context by recency.
    first = {}
    for metric in by_concept.values():
        first.setdefault(metric.company_id, metric)
    selected = list(first.values())
    selected_ids = {id(metric) for metric in selected}
    selected.extend(metric for metric in by_concept.values() if id(metric) not in selected_ids)
    facts = [metric.model_dump(mode='json') for metric in selected[:36]]
    registry = registry or EvidenceRegistry([*sources, *web_sources],
                                            (metric for metric in selected if getattr(metric, 'metric_id', None)))
    fact_ids = {identifier for fact in facts for identifier in fact['evidence_ids']}
    metric_ids = {fact['metric_id'] for fact in facts if fact.get('metric_id')}
    news_context = (news.model_dump(mode='json', include={'insights', 'limitations'})
                    if news and not historical_day else None)
    news_ids = {identifier for insight in (news_context or {}).get('insights', [])
                for identifier in insight['evidence_ids']}
    available_leads = [source for source in web_sources if source.quoted_text and not historical_day]
    selected_leads = {source.source_id: source for source in available_leads[:10]}
    for source_id in sorted(news_ids):
        source = registry.get(source_id)
        if source and source_id not in official_ids and source.quoted_text:
            selected_leads.setdefault(source_id, source)
    leads = [{'source_id': source.source_id, 'publisher': source.publisher[:100],
              'date': source.publication_date, 'issuer_id': source.company_id,
              'title': source.title[:140], 'excerpt': source.quoted_text[:420]}
             for source in selected_leads.values()]
    lead_ids = set(selected_leads)
    if news_context:
        news_context['insights'] = [insight for insight in news_context['insights']
                                    if set(insight['evidence_ids']) <= fact_ids | lead_ids]
    archives = []
    for report in input_reports:
        available_ids = {source.source_id for source in report.sources}
        claims = [claim for claim in report.claims
                  if claim.verification_status == 'supported'
                  and claim.claim_type in {'disclosed_fact', 'calculation'}
                  and claim.evidence_ids and set(claim.evidence_ids) <= available_ids]
        archives.append({
            'report_id': report.report_id, 'generated_at': report.generated_at.isoformat(),
            'as_of_date': report.data_as_of,
            'issuers': [company.company_name[:100] for company in report.companies],
            'verified_claims': [{'text': claim.text[:230], 'evidence_ids': claim.evidence_ids}
                                for claim in claims[:8]],
            'verification_gaps': [text[:200] for text in report.limitations[:3]],
        })
    coverage = 'financial_and_external' if fact_ids and leads else 'financial_only' if fact_ids else 'limited'
    archive_ids = {ref for archive in archives for claim in archive['verified_claims']
                   for ref in claim['evidence_ids'] if registry.get(ref)}
    if not fact_ids and not leads and not archive_ids:
        issues.append('Investment synthesis had no eligible underlying evidence.')
        missing = []
        if not fact_ids:
            missing.append('No verified financial facts were available for the investment analysis. '
                           'Check the cited filings and reporting periods in the financial section.')
        if not leads:
            missing.append('No usable external evidence was available to test the investment thesis. '
                           'Check News & Market Intelligence for search results and limitations.')
        return InvestmentAnalysis(stance='insufficient', confidence='low', points=[], missing_inputs=missing,
                                  evidence_coverage=coverage,
                                  observation_reason='The available evidence does not support a directional view. '
                                                     'Monitor verifiable filings, cash flow, and external evidence.')
    valuation_ids = {identifier for item in valuations for measure in item.measures
                     for identifier in measure.evidence_ids}
    valuation_ids.update(item.price_source_id for item in valuations if item.price_source_id)
    progress('investment_analysis')
    try:
        with limits.provider_call('nebius', stage='investment_synthesis') as event:
            context = {
                'article_language': 'en',
                'question': query[:2000], 'core_question': plan.core_question,
                'verification_questions': plan.verification_questions,
                'investment_intent': getattr(plan, 'investment_intent', False),
                'verified_financial_facts': facts, 'unverified_external_leads': leads,
                'saved_report_context': archives,
                'research_gaps': issues[:8],
                'news_analysis': news_context,
                'valuation_research': [item.model_dump(mode='json') for item in valuations],
            }
            draft = model.synthesize_investment(context, limits)
            draft_incomplete = bool(event and event.error_code == 'partial_investment_draft')
            if draft_incomplete:
                issues.append('Some investment draft sections were incomplete. Usable sections were retained.')
            draft = draft.model_copy(update={'evidence_coverage': coverage,
                                           'stance': 'insufficient' if draft.stance == 'provisional_neutral' else draft.stance})
    except (ProviderError, ResourceLimitReached):
        issues.append('Investment synthesis was unavailable.')
        return InvestmentAnalysis(stance='insufficient', confidence='low', points=[], evidence_coverage=coverage,
                                  missing_inputs=['The investment synthesis did not complete.'])
    allowed_ids = fact_ids | lead_ids | valuation_ids | archive_ids
    citations = {}
    unmatched_citations = {}
    for index, point in enumerate(draft.points):
        refs, missing = registry.resolve(point.evidence_ids, allowed_ids, metric_ids)
        if refs and len(refs) <= 8:
            citations[str(index)] = refs
            if missing:
                unmatched_citations[str(index)] = missing
    required = set(issuer_ids) or {fact['company_id'] for fact in facts}
    if on_draft is not None:
        job = prepare_review(draft, context, required, citations, limits.model or model.active_model(),
                             unmatched_citations=unmatched_citations)
        job['generation_partial'] = draft_incomplete
        on_draft(job)
        analysis, _ = summarize(job, pending=True)
        return analysis
    candidates = [draft.points[int(index)].model_copy(update={'evidence_ids': refs})
                  for index, refs in citations.items()]
    reviewed = []
    if candidates:
        try:
            with limits.provider_call('nebius', stage='investment_review'):
                accepted = model.review_investment(context, draft.stance, candidates, sorted(required), limits)
        except (ProviderError, ResourceLimitReached):
            accepted = set()
        reviewed = [point for index, point in enumerate(candidates) if index in accepted]
        # A rejected thesis can change the meaning of a multi-point stance.
        if supported_direction(draft.stance, reviewed, facts, required) :
            return draft.model_copy(update={'points': reviewed,
                                           'confidence': 'medium' if draft.confidence == 'high' else draft.confidence})
    issues.append('The available evidence did not establish an investment stance.')
    return draft.model_copy(update={'stance': 'insufficient', 'confidence': 'low',
                                   'points': [point for point in reviewed if point.topic != 'thesis'],
                                   'missing_inputs': draft.missing_inputs or ['Relevant corroboration for the thesis.']})

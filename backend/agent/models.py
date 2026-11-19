from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

TaskStatus = Literal['pending', 'researching', 'verifying', 'completed', 'partial', 'failed']
EvidenceStatus = Literal['supported', 'contradicted', 'insufficient']


class Company(BaseModel):
    company_id: str
    company_name: str
    ticker: str | None = None
    exchange: str | None = None
    country: str | None = None
    cik: str | None = None
    resolution_status: Literal['resolved', 'ambiguous', 'unverified']


class Metric(BaseModel):
    metric_id: str
    company_id: str
    metric_name: str
    value: float
    currency: str | None = None
    unit: str
    period_start: str | None = None
    period_end: str
    fiscal_period: str
    accounting_basis: str
    actual_or_guidance: Literal['actual', 'guidance', 'analyst_estimate', 'model_inference']
    evidence_ids: list[str]
    capex_scope: str | None = None
    duration_kind: Literal['quarter', 'ytd', 'year'] | None = None
    calculation_method: Literal['quarter_difference', 'operating_cash_flow_less_capex', 'growth'] | None = None


class Source(BaseModel):
    source_id: str
    title: str
    publisher: str
    url: str
    publication_date: str | None = None
    retrieved_at: datetime | None = None
    source_type: str
    quoted_text: str | None = None
    company_id: str | None = None
    metric_name: str | None = None
    numeric_value: float | None = None
    unit: str | None = None
    period_end: str | None = None
    structured_locator: str | None = None
    issuer_name: str | None = None
    period_start: str | None = None
    accounting_basis: str | None = None
    actual_or_guidance: str | None = None
    capex_scope: str | None = None
    financial_concept: str | None = None
    xbrl_tag: str | None = None
    tag_definition: str | None = None
    original_numeric_value: str | None = None


class Claim(BaseModel):
    claim_id: str
    text: str
    verification_status: EvidenceStatus
    evidence_ids: list[str]
    limitations: list[str] = Field(default_factory=list)
    company_id: str | None = None
    metric_name: str | None = None
    numeric_value: float | None = None
    unit: str | None = None
    period_end: str | None = None
    period_start: str | None = None
    accounting_basis: str | None = None
    actual_or_guidance: str | None = None
    capex_scope: str | None = None
    claim_type: Literal['disclosed_fact', 'calculation', 'analysis', 'scenario', 'evidence_gap'] | None = None


class ProviderActivity(BaseModel):
    enabled: bool = False
    calls: int = 0
    succeeded: int = 0
    failed: int = 0
    mock_used: bool = False
    http_attempts: int | None = None
    http_responses: int | None = None


class ProviderEvent(BaseModel):
    provider: str
    stage: str
    elapsed_ms: int = 0
    http_attempts: int = 0
    http_responses: int = 0
    outcome: Literal['succeeded', 'failed'] = 'succeeded'
    company_id: str | None = None
    query: str | None = None
    topic: str | None = None
    result_count: int | None = None
    usable_results: int | None = None
    adopted_source_ids: list[str] = Field(default_factory=list)
    news_context_source_ids: list[str] = Field(default_factory=list)
    news_selected_source_ids: list[str] = Field(default_factory=list)
    news_cited_source_ids: list[str] = Field(default_factory=list)
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error_code: str | None = None
    finish_reason: str | None = None
    http_status: int | None = None
    max_output_tokens: int | None = None
    batch_id: str | None = None
    retry_wait_seconds: float | None = None
    filing_types: list[str] = Field(default_factory=list)
    filings_retrieved: int | None = None
    filings_parsed: int | None = None
    filing_parse_failures: int | None = None
    parsed: bool | None = None
    extracted: int | None = None
    verified: int | None = None


class InvestmentPoint(BaseModel):
    topic: Literal['thesis', 'outlook', 'financial_quality', 'valuation', 'bull_case',
                   'base_case', 'bear_case', 'risk', 'catalyst', 'counterargument', 'invalidation',
                   'operating_state', 'market_expectations', 'supporting_case', 'opposing_case',
                   'tradeoff', 'key_factor', 'horizon']
    text: str = Field(min_length=1, max_length=900)
    evidence_ids: list[str] = Field(max_length=8)


class InvestmentAnalysis(BaseModel):
    stance: Literal['bullish', 'cautiously_bullish', 'bearish', 'cautiously_bearish', 'neutral', 'provisional_neutral', 'insufficient']
    confidence: Literal['low', 'medium', 'high']
    points: list[InvestmentPoint] = Field(max_length=16)
    missing_inputs: list[str] = Field(max_length=10)
    narrative: str | None = Field(default=None, max_length=10000)
    evidence_coverage: Literal['financial_and_external', 'financial_only', 'limited'] | None = None
    observation_reason: str | None = Field(default=None, max_length=700)


class InvestmentNarrativeSection(InvestmentPoint):
    review_verdict: str = 'pending'
    review_reason: str | None = None
    suggested_revision: str | None = None


class InvestmentNarrative(BaseModel):
    sections: list[InvestmentNarrativeSection]
    language: Literal['en', 'zh'] = 'en'


class InvestmentReviewStatus(BaseModel):
    generation: Literal['generation_completed', 'generation_partial', 'generation_failed']
    review: Literal['review_pending', 'review_running', 'review_completed', 'review_partial',
                    'review_failed_technical', 'review_blocked']
    opinion: Literal['provisional', 'approved', 'evidence_insufficient']
    verdicts: dict[str, str] = Field(default_factory=dict)
    reasons: dict[str, str] = Field(default_factory=dict)
    suggested_revisions: dict[str, str] = Field(default_factory=dict)
    batch_status: dict[str, str] = Field(default_factory=dict)


class ValuationMeasure(BaseModel):
    name: str
    value: float
    unit: str
    formula: str
    inputs: dict[str, float]
    evidence_ids: list[str]
    basis: Literal['trailing_actual', 'fiscal_year_actual', 'instant_actual', 'forward_estimate']
    period_end: str


class IssuerValuation(BaseModel):
    company_id: str
    status: Literal['completed', 'partial', 'data_insufficient', 'not_executed']
    price: float | None = None
    currency: str | None = None
    trade_date: str | None = None
    price_source_id: str | None = None
    measures: list[ValuationMeasure] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    note: str | None = None
    qualitative: str | None = None
    qualitative_evidence_ids: list[str] = Field(default_factory=list)


class ResearchQuality(BaseModel):
    core_gaps: list[str] = Field(default_factory=list)
    supplemental_gaps: list[str] = Field(default_factory=list)
    historical_conflicts: int = 0


NewsCategory = Literal['operations_strategy', 'earnings', 'products_technology', 'industry',
                       'competition', 'analyst_views', 'macro', 'policy_regulation', 'geopolitics', 'other']


class NewsItem(BaseModel):
    source_id: str
    category: NewsCategory
    summary: str
    excerpt: str | None = None
    attribution: Literal['management_statement', 'analyst_view', 'reported_event', 'unclassified']
    publication_date: str | None = None
    publication_basis: Literal['publisher_metadata', 'search_metadata', 'unknown'] = 'unknown'
    event_date: str | None = None
    publisher_excerpt_matched: bool = False
    related_source_ids: list[str] = Field(default_factory=list)


class NewsInsight(BaseModel):
    kind: Literal['positive', 'negative', 'cross_news', 'business', 'financial', 'market_expectations', 'synthesis']
    text: str = Field(min_length=1, max_length=1600)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class NewsAnalysis(BaseModel):
    generation_status: Literal['news_collected', 'news_analysis_completed', 'synthesis_completed',
                               'generation_truncated', 'generation_failed', 'budget_unavailable',
                               'evidence_insufficient'] | None = None
    failed_stage: Literal['analysis', 'article'] | None = None
    status: Literal['completed', 'partial', 'search_failed', 'no_results', 'not_executed']
    overall_assessment: Literal['positive', 'neutral', 'cautious', 'mixed', 'insufficient'] = 'insufficient'
    confidence: Literal['low', 'medium'] = 'low'
    as_of: str
    news_items: list[NewsItem] = Field(default_factory=list)
    insights: list[NewsInsight] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class Report(BaseModel):
    report_id: str
    task_id: str
    query: str
    generated_at: datetime
    data_as_of: str | None = None
    data_mode: Literal['mock', 'live', 'mixed', 'unverified']
    providers: dict[str, ProviderActivity] = Field(default_factory=dict)
    companies: list[Company]
    metrics: list[Metric]
    claims: list[Claim]
    sources: list[Source]
    limitations: list[str]
    input_report_ids: list[str] = Field(default_factory=list)
    investment_analysis: InvestmentAnalysis | None = None
    valuation_status: Literal['completed', 'partial', 'data_insufficient', 'not_executed'] | None = None
    valuations: list[IssuerValuation] = Field(default_factory=list)
    news_analysis: NewsAnalysis | None = None
    research_quality: ResearchQuality | None = None
    investment_narrative: InvestmentNarrative | None = None
    investment_draft: InvestmentAnalysis | None = None
    investment_review: InvestmentReviewStatus | None = None
    provider_events: list[ProviderEvent] = Field(default_factory=list)
    status: TaskStatus

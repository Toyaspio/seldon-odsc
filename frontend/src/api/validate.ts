/**
 * Hand-rolled runtime validation against the frozen contracts.
 * A response that does not match the schema is surfaced as an error,
 * never rendered as if it were valid data.
 */
import {
  ACTUAL_OR_GUIDANCE,
  RESOLUTION_STATUSES,
  TASK_STATUSES,
  VERIFICATION_STATUSES,
  type Claim,
  type Company,
  type Metric,
  type Report,
  type Source,
  type TaskInfo,
  type TaskStatus,
} from '../types/api'

export class ValidationError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ValidationError'
  }
}

function asObject(value: unknown, what: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new ValidationError(`${what} is not an object`)
  }
  return value as Record<string, unknown>
}

function str(value: unknown, what: string): string {
  if (typeof value !== 'string') throw new ValidationError(`${what} is not a string`)
  return value
}

function optStr(value: unknown, what: string): string | null {
  if (value === null || value === undefined) return null
  if (typeof value !== 'string') throw new ValidationError(`${what} is not a string or null`)
  return value
}

function num(value: unknown, what: string): number {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    throw new ValidationError(`${what} is not a number`)
  }
  return value
}

function optNum(value: unknown, what: string): number | null {
  if (value === null || value === undefined) return null
  return num(value, what)
}

function strArray(value: unknown, what: string): string[] {
  if (!Array.isArray(value)) throw new ValidationError(`${what} is not an array`)
  return value.map((item, i) => str(item, `${what}[${i}]`))
}

function enumValue<T extends string>(value: unknown, allowed: readonly T[], what: string): T {
  const s = str(value, what)
  if (!(allowed as readonly string[]).includes(s)) {
    throw new ValidationError(`${what} has unexpected value "${s}"`)
  }
  return s as T
}

function isRecordArray(value: unknown, what: string): Record<string, unknown>[] {
  if (!Array.isArray(value)) throw new ValidationError(`${what} is not an array`)
  return value.map((item, i) => asObject(item, `${what}[${i}]`))
}

export function parseTaskStatus(value: unknown, what: string): TaskStatus {
  return enumValue(value, TASK_STATUSES, what)
}

export function parseTaskInfo(value: unknown): TaskInfo {
  const o = asObject(value, 'task')
  const usage = o.resource_usage == null ? null : asObject(o.resource_usage, 'task.resource_usage')
  return {
    resource_usage: usage ? {
      model: optStr(usage.model, 'task.resource_usage.model'),
      prompt_tokens: num(usage.prompt_tokens, 'task.resource_usage.prompt_tokens'),
      completion_tokens: num(usage.completion_tokens, 'task.resource_usage.completion_tokens'),
      estimated_cost_usd: optNum(usage.estimated_cost_usd, 'task.resource_usage.estimated_cost_usd'),
    } : undefined,
    task_id: str(o.task_id, 'task.task_id'),
    status: parseTaskStatus(o.status, 'task.status'),
    progress: num(o.progress, 'task.progress'),
    current_step: str(o.current_step, 'task.current_step'),
    created_at: str(o.created_at, 'task.created_at'),
    updated_at: str(o.updated_at, 'task.updated_at'),
    report_id: optStr(o.report_id, 'task.report_id'),
    error_code: optStr(o.error_code, 'task.error_code'),
    error_message: optStr(o.error_message, 'task.error_message'),
  }
}

export function parseCreateTaskResponse(value: unknown): { task_id: string; status: TaskStatus } {
  const o = asObject(value, 'create-task response')
  return {
    task_id: str(o.task_id, 'task_id'),
    status: parseTaskStatus(o.status, 'status'),
  }
}

export function parseCompany(value: unknown): Company {
  const o = asObject(value, 'company')
  return {
    company_id: str(o.company_id, 'company.company_id'),
    company_name: str(o.company_name, 'company.company_name'),
    ticker: optStr(o.ticker, 'company.ticker'),
    exchange: optStr(o.exchange, 'company.exchange'),
    country: optStr(o.country, 'company.country'),
    cik: optStr(o.cik, 'company.cik'),
    resolution_status: enumValue(o.resolution_status, RESOLUTION_STATUSES, 'company.resolution_status'),
  }
}

export function parseMetric(value: unknown): Metric {
  const o = asObject(value, 'metric')
  return {
    metric_id: str(o.metric_id, 'metric.metric_id'),
    company_id: str(o.company_id, 'metric.company_id'),
    metric_name: str(o.metric_name, 'metric.metric_name'),
    value: num(o.value, 'metric.value'),
    currency: optStr(o.currency, 'metric.currency'),
    unit: str(o.unit, 'metric.unit'),
    period_start: optStr(o.period_start, 'metric.period_start'),
    period_end: str(o.period_end, 'metric.period_end'),
    fiscal_period: str(o.fiscal_period, 'metric.fiscal_period'),
    accounting_basis: str(o.accounting_basis, 'metric.accounting_basis'),
    actual_or_guidance: enumValue(o.actual_or_guidance, ACTUAL_OR_GUIDANCE, 'metric.actual_or_guidance'),
    evidence_ids: strArray(o.evidence_ids, 'metric.evidence_ids'),
    capex_scope: optStr(o.capex_scope, 'metric.capex_scope'),
    duration_kind: optStr(o.duration_kind, 'metric.duration_kind'),
    calculation_method: optStr(o.calculation_method, 'metric.calculation_method'),
  }
}

export function parseClaim(value: unknown): Claim {
  const o = asObject(value, 'claim')
  return {
    claim_id: str(o.claim_id, 'claim.claim_id'),
    text: str(o.text, 'claim.text'),
    verification_status: enumValue(o.verification_status, VERIFICATION_STATUSES, 'claim.verification_status'),
    evidence_ids: strArray(o.evidence_ids, 'claim.evidence_ids'),
    limitations: 'limitations' in o ? strArray(o.limitations, 'claim.limitations') : [],
    company_id: optStr(o.company_id, 'claim.company_id'),
    metric_name: optStr(o.metric_name, 'claim.metric_name'),
    numeric_value: optNum(o.numeric_value, 'claim.numeric_value'),
    unit: optStr(o.unit, 'claim.unit'),
    period_end: optStr(o.period_end, 'claim.period_end'),
    period_start: optStr(o.period_start, 'claim.period_start'),
    accounting_basis: optStr(o.accounting_basis, 'claim.accounting_basis'),
    actual_or_guidance: o.actual_or_guidance == null ? null : enumValue(o.actual_or_guidance, ACTUAL_OR_GUIDANCE, 'claim.actual_or_guidance'),
    claim_type: optStr(o.claim_type, 'claim.claim_type'),
    capex_scope: optStr(o.capex_scope, 'claim.capex_scope'),
  }
}

export function parseSource(value: unknown): Source {
  const o = asObject(value, 'source')
  return {
    source_id: str(o.source_id, 'source.source_id'),
    title: str(o.title, 'source.title'),
    publisher: str(o.publisher, 'source.publisher'),
    url: str(o.url, 'source.url'),
    publication_date: optStr(o.publication_date, 'source.publication_date'),
    retrieved_at: optStr(o.retrieved_at, 'source.retrieved_at'),
    source_type: str(o.source_type, 'source.source_type'),
    quoted_text: optStr(o.quoted_text, 'source.quoted_text'),
    company_id: optStr(o.company_id, 'source.company_id'),
    metric_name: optStr(o.metric_name, 'source.metric_name'),
    numeric_value: optNum(o.numeric_value, 'source.numeric_value'),
    unit: optStr(o.unit, 'source.unit'),
    period_end: optStr(o.period_end, 'source.period_end'),
    structured_locator: optStr(o.structured_locator, 'source.structured_locator'),
    capex_scope: optStr(o.capex_scope, 'source.capex_scope'),
    financial_concept: optStr(o.financial_concept, 'source.financial_concept'),
    xbrl_tag: optStr(o.xbrl_tag, 'source.xbrl_tag'),
    tag_definition: optStr(o.tag_definition, 'source.tag_definition'),
    original_numeric_value: optStr(o.original_numeric_value, 'source.original_numeric_value'),
    issuer_name: optStr(o.issuer_name, 'source.issuer_name'),
    period_start: optStr(o.period_start, 'source.period_start'),
    accounting_basis: optStr(o.accounting_basis, 'source.accounting_basis'),
    actual_or_guidance: o.actual_or_guidance == null ? null : enumValue(o.actual_or_guidance, ACTUAL_OR_GUIDANCE, 'source.actual_or_guidance'),
  }
}

function parseRows<T>(value: unknown, parse: (row: Record<string, unknown>) => T): T[] {
  if (!Array.isArray(value)) return []
  const rows: T[] = []
  for (const entry of value) {
    try {
      rows.push(parse(asObject(entry, 'report row')))
    } catch {
      // A damaged row must not hide the rest of a saved report.
    }
  }
  return rows
}

function parseInvestment(value: unknown): Report['investment_analysis'] {
  if (value == null) return null
  const item = asObject(value, 'investment analysis')
  return {
    stance: enumValue(item.stance, ['bullish', 'cautiously_bullish', 'bearish', 'cautiously_bearish', 'neutral', 'provisional_neutral', 'insufficient'] as const, 'stance'),
    confidence: enumValue(item.confidence, ['low', 'medium', 'high'] as const, 'confidence'),
    points: parseRows(item.points, (point) => ({
      topic: str(point.topic, 'topic'),
      text: str(point.text, 'analysis'),
      evidence_ids: strArray(point.evidence_ids, 'investment evidence'),
    })),
    missing_inputs: strArray(item.missing_inputs ?? [], 'missing investment inputs'),
    narrative: optStr(item.narrative, 'investment narrative'),
    observation_reason: optStr(item.observation_reason, 'observation reason'),
    evidence_coverage: (['financial_and_external', 'financial_only', 'limited'] as const)
      .find((value) => value === item.evidence_coverage) ?? null,
  }
}

function parseProviderEvents(value: unknown): NonNullable<Report['provider_events']> {
  if (!Array.isArray(value)) return []
  return value.flatMap((entry) => {
    try {
      const item = asObject(entry, 'provider event')
      const optionalNumber = (key: string) => item[key] == null ? null : num(item[key], key)
      return [{
        provider: str(item.provider, 'provider'), stage: str(item.stage, 'stage'),
        outcome: enumValue(item.outcome, ['succeeded', 'failed'] as const, 'provider outcome'),
        elapsed_ms: num(item.elapsed_ms, 'elapsed_ms'),
        http_attempts: num(item.http_attempts, 'http_attempts'),
        http_responses: num(item.http_responses, 'http_responses'),
        company_id: optStr(item.company_id, 'company_id'),
        query: optStr(item.query, 'query'), topic: optStr(item.topic, 'topic'), model: optStr(item.model, 'model'),
        error_code: optStr(item.error_code, 'error_code'),
        finish_reason: optStr(item.finish_reason, 'finish_reason'),
        http_status: optionalNumber('http_status'), max_output_tokens: optionalNumber('max_output_tokens'),
        batch_id: optStr(item.batch_id, 'batch_id'), retry_wait_seconds: optionalNumber('retry_wait_seconds'),
        result_count: optionalNumber('result_count'), usable_results: optionalNumber('usable_results'),
        filings_parsed: optionalNumber('filings_parsed'), filing_parse_failures: optionalNumber('filing_parse_failures'), prompt_tokens: optionalNumber('prompt_tokens'),
        completion_tokens: optionalNumber('completion_tokens'), filings_retrieved: optionalNumber('filings_retrieved'),
        extracted: optionalNumber('extracted'), verified: optionalNumber('verified'),
        parsed: typeof item.parsed === 'boolean' ? item.parsed : null,
        adopted_source_ids: strArray(item.adopted_source_ids ?? [], 'adopted sources'),
        news_context_source_ids: strArray(item.news_context_source_ids ?? [], 'news context sources'),
        news_selected_source_ids: strArray(item.news_selected_source_ids ?? [], 'selected news sources'),
        news_cited_source_ids: strArray(item.news_cited_source_ids ?? [], 'cited news sources'),
        filing_types: strArray(item.filing_types ?? [], 'filing types'),
      }]
    } catch {
      return []
    }
  })
}

function optionalInvestment(value: unknown): Report['investment_analysis'] {
  try {
    return parseInvestment(value)
  } catch {
    return null
  }
}

function parseReview(value: unknown): Report['investment_review'] {
  if (value == null) return null
  try {
    const item = asObject(value, 'investment review')
    const records = (value: unknown) => Object.fromEntries(
      Object.entries(asObject(value ?? {}, 'review details')).filter((entry): entry is [string, string] =>
        typeof entry[1] === 'string'))
    return {
      generation: str(item.generation, 'generation'),
      review: str(item.review, 'review'),
      opinion: str(item.opinion, 'opinion'),
      verdicts: records(item.verdicts),
      reasons: records(item.reasons),
      batch_status: records(item.batch_status),
    }
  } catch {
    return null
  }
}

const VALUATION_STATES = ['completed', 'partial', 'data_insufficient', 'not_executed'] as const

function parseValuations(value: unknown): NonNullable<Report['valuations']> {
  if (!Array.isArray(value)) return []
  return value.flatMap((entry) => {
    try {
      const row = asObject(entry, 'issuer valuation')
      const measures = isRecordArray(row.measures ?? [], 'valuation measures').map((item) => ({
        name: str(item.name, 'valuation name'), value: num(item.value, 'valuation value'),
        unit: str(item.unit, 'valuation unit'), formula: str(item.formula, 'valuation formula'),
        inputs: Object.fromEntries(Object.entries(asObject(item.inputs, 'valuation inputs'))
          .map(([key, value]) => [key, num(value, key)])),
        evidence_ids: strArray(item.evidence_ids, 'valuation evidence'),
        basis: str(item.basis, 'valuation basis'), period_end: str(item.period_end, 'period end'),
      }))
      return [{
        company_id: str(row.company_id, 'ticker'), status: enumValue(row.status, VALUATION_STATES, 'valuation status'),
        price: row.price == null ? null : num(row.price, 'closing price'),
        currency: optStr(row.currency, 'currency'), trade_date: optStr(row.trade_date, 'trade date'),
        price_source_id: optStr(row.price_source_id, 'price evidence'),
        missing: strArray(row.missing ?? [], 'missing valuation inputs'),
        note: optStr(row.note, 'valuation note'), qualitative: optStr(row.qualitative, 'qualitative valuation'),
        qualitative_evidence_ids: strArray(row.qualitative_evidence_ids ?? [], 'qualitative evidence'),
        measures,
      }]
    } catch {
      return []
    }
  })
}

function parseResearchQuality(value: unknown): Report['research_quality'] {
  if (value == null) return null
  try {
    const row = asObject(value, 'research quality')
    return {
      core_gaps: strArray(row.core_gaps ?? [], 'core gaps'),
      supplemental_gaps: strArray(row.supplemental_gaps ?? [], 'supplemental gaps'),
      historical_conflicts: num(row.historical_conflicts ?? 0, 'historical conflicts'),
    }
  } catch {
    return null
  }
}

function parseNews(value: unknown): Report['news_analysis'] {
  if (value == null) return null
  try {
    const row = asObject(value, 'news analysis')
    return {
      status: enumValue(row.status, ['completed', 'partial', 'search_failed', 'no_results', 'not_executed'] as const, 'news status'),
      generation_status: optStr(row.generation_status, 'news generation status'),
      failed_stage: optStr(row.failed_stage, 'failed news stage'),
      overall_assessment: enumValue(row.overall_assessment,
        ['positive', 'neutral', 'cautious', 'mixed', 'insufficient'] as const, 'news assessment'),
      confidence: enumValue(row.confidence, ['low', 'medium'] as const, 'news confidence'),
      as_of: str(row.as_of, 'news cutoff'),
      news_items: parseRows(row.news_items, (item) => ({
        source_id: str(item.source_id, 'news source'), category: str(item.category, 'news category'),
        summary: str(item.summary, 'news summary'), excerpt: optStr(item.excerpt, 'news excerpt'),
        attribution: str(item.attribution, 'news attribution'),
        publication_date: optStr(item.publication_date, 'publication date'),
        publication_basis: str(item.publication_basis, 'publication source'),
        event_date: optStr(item.event_date, 'event date'),
        publisher_excerpt_matched: item.publisher_excerpt_matched === true,
        related_source_ids: strArray(item.related_source_ids ?? [], 'related sources'),
      })),
      insights: parseRows(row.insights, (item) => ({
        kind: str(item.kind, 'news insight kind'), text: str(item.text, 'news insight'),
        evidence_ids: strArray(item.evidence_ids, 'insight evidence'),
      })),
      limitations: strArray(row.limitations ?? [], 'news limitations'),
    }
  } catch {
    return null
  }
}

function parseInvestmentArticle(value: unknown): Report['investment_narrative'] {
  if (value == null) return null
  try {
    const article = asObject(value, 'investment article')
    return {
      language: enumValue(article.language, ['en', 'zh'] as const, 'article language'),
      sections: parseRows(article.sections, (entry) => ({
        topic: str(entry.topic, 'section topic'), text: str(entry.text, 'section text'),
        evidence_ids: strArray(entry.evidence_ids, 'section citations'),
        review_verdict: str(entry.review_verdict, 'section review'),
        review_reason: optStr(entry.review_reason, 'review reason'),
        suggested_revision: optStr(entry.suggested_revision, 'suggested revision'),
      })),
    }
  } catch {
    return null
  }
}

export function parseReport(value: unknown): Report {
  const o = asObject(value, 'report')
  const providers = o.providers == null ? {} : asObject(o.providers, 'report.providers')
  const activity = Object.fromEntries(Object.entries(providers).map(([name, value]) => {
    const provider = asObject(value, 'provider activity')
    return [name, {
      enabled: provider.enabled === true,
      calls: num(provider.calls, 'provider.calls'),
      succeeded: num(provider.succeeded, 'provider.succeeded'),
      failed: num(provider.failed, 'provider.failed'),
      mock_used: provider.mock_used === true,
      http_attempts: provider.http_attempts == null ? null : num(provider.http_attempts, 'provider.http_attempts'),
      http_responses: provider.http_responses == null ? null : num(provider.http_responses, 'provider.http_responses'),
    }]
  }))
  const mode = enumValue(o.data_mode, ['mock', 'live', 'mixed', 'unverified'] as const, 'report.data_mode')
  return {
    report_id: str(o.report_id, 'report.report_id'),
    task_id: str(o.task_id, 'report.task_id'),
    query: str(o.query, 'report.query'),
    generated_at: str(o.generated_at, 'report.generated_at'),
    data_as_of: optStr(o.data_as_of, 'report.data_as_of'),
    data_mode: mode === 'live' && !Object.keys(activity).length ? 'unverified' : mode,
    providers: activity,
    input_report_ids: o.input_report_ids == null ? [] : strArray(o.input_report_ids, "input reports"),
    investment_analysis: optionalInvestment(o.investment_analysis),
    valuation_status: VALUATION_STATES.find((state) => state === o.valuation_status) ?? null,
    valuations: parseValuations(o.valuations),
    news_analysis: parseNews(o.news_analysis),
    research_quality: parseResearchQuality(o.research_quality),
    investment_draft: optionalInvestment(o.investment_draft),
    investment_review: parseReview(o.investment_review),
    investment_narrative: parseInvestmentArticle(o.investment_narrative),
    provider_events: parseProviderEvents(o.provider_events),
    companies: parseRows(o.companies, parseCompany),
    metrics: parseRows(o.metrics, parseMetric),
    claims: parseRows(o.claims, parseClaim),
    sources: parseRows(o.sources, parseSource),
    limitations: strArray(o.limitations, 'report.limitations'),
    status: parseTaskStatus(o.status, 'report.status'),
  }
}

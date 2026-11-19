/**
 * Types mirroring contracts/openapi.yaml and contracts/report.schema.json.
 * These are the frozen phase-one contracts; do not invent fields beyond them.
 */

export const TASK_STATUSES = [
  'pending',
  'researching',
  'verifying',
  'completed',
  'partial',
  'failed',
] as const
export type TaskStatus = (typeof TASK_STATUSES)[number]

export const TERMINAL_TASK_STATUSES: readonly TaskStatus[] = ['completed', 'partial', 'failed']

export const VERIFICATION_STATUSES = ['supported', 'contradicted', 'insufficient'] as const
export type VerificationStatus = (typeof VERIFICATION_STATUSES)[number]

export const ACTUAL_OR_GUIDANCE = [
  'actual',
  'guidance',
  'analyst_estimate',
  'model_inference',
] as const
export type ActualOrGuidance = (typeof ACTUAL_OR_GUIDANCE)[number]

export const RESOLUTION_STATUSES = ['resolved', 'ambiguous', 'unverified'] as const
export type ResolutionStatus = (typeof RESOLUTION_STATUSES)[number]

export type DataMode = 'mock' | 'live' | 'mixed' | 'unverified'

export interface ProviderActivity {
  enabled: boolean
  calls: number
  succeeded: number
  failed: number
  mock_used: boolean
  http_attempts?: number | null
  http_responses?: number | null
}

export interface Company {
  company_id: string
  company_name: string
  ticker: string | null
  exchange: string | null
  country: string | null
  cik: string | null
  resolution_status: ResolutionStatus
}

export interface Metric {
  metric_id: string
  company_id: string
  metric_name: string
  value: number
  currency: string | null
  unit: string
  period_start: string | null
  period_end: string
  fiscal_period: string
  accounting_basis: string
  actual_or_guidance: ActualOrGuidance
  evidence_ids: string[]
  capex_scope?: string | null
  duration_kind?: string | null
  calculation_method?: string | null
}

export interface Claim {
  claim_id: string
  text: string
  verification_status: VerificationStatus
  evidence_ids: string[]
  limitations: string[]
  company_id: string | null
  metric_name: string | null
  numeric_value: number | null
  unit: string | null
  period_end: string | null
  /** Optional fields added by the 2026-11-14 contract update; tolerated when absent. */
  period_start?: string | null
  accounting_basis?: string | null
  actual_or_guidance?: ActualOrGuidance | null
  claim_type?: string | null
  capex_scope?: string | null
}

export interface Source {
  source_id: string
  title: string
  publisher: string
  url: string
  publication_date: string | null
  retrieved_at: string | null
  source_type: string
  quoted_text: string | null
  company_id: string | null
  metric_name: string | null
  numeric_value: number | null
  unit: string | null
  period_end: string | null
  structured_locator: string | null
  capex_scope?: string | null
  financial_concept?: string | null
  xbrl_tag?: string | null
  tag_definition?: string | null
  original_numeric_value?: string | null
  /** Optional fields added by the 2026-11-14 contract update; tolerated when absent. */
  issuer_name?: string | null
  period_start?: string | null
  accounting_basis?: string | null
  actual_or_guidance?: ActualOrGuidance | null
}

export interface InvestmentAnalysis {
  stance: 'bullish' | 'cautiously_bullish' | 'bearish' | 'cautiously_bearish' | 'neutral' | 'provisional_neutral' | 'insufficient'
  confidence: 'low' | 'medium' | 'high'
  points: { topic: string; text: string; evidence_ids: string[] }[]
  missing_inputs: string[]
  narrative?: string | null
  observation_reason?: string | null
  evidence_coverage?: 'financial_and_external' | 'financial_only' | 'limited' | null
}

export interface ProviderEvent {
  provider: string
  stage: string
  outcome: 'succeeded' | 'failed'
  elapsed_ms: number
  http_attempts: number
  http_responses: number
  company_id?: string | null
  query?: string | null
  topic?: string | null
  result_count?: number | null
  usable_results?: number | null
  adopted_source_ids?: string[]
  news_context_source_ids?: string[]
  news_selected_source_ids?: string[]
  news_cited_source_ids?: string[]
  model?: string | null
  prompt_tokens?: number | null
  completion_tokens?: number | null
  error_code?: string | null
  finish_reason?: string | null
  http_status?: number | null
  max_output_tokens?: number | null
  batch_id?: string | null
  retry_wait_seconds?: number | null
  filing_types?: string[]
  filings_retrieved?: number | null
  filings_parsed?: number | null
  filing_parse_failures?: number | null
  parsed?: boolean | null
  extracted?: number | null
  verified?: number | null
}

export type ValuationStatus = 'completed' | 'partial' | 'data_insufficient' | 'not_executed'

export interface IssuerValuation {
  company_id: string
  status: ValuationStatus
  price?: number | null
  currency?: string | null
  trade_date?: string | null
  price_source_id?: string | null
  measures: {
    name: string
    value: number
    unit: string
    formula: string
    inputs: Record<string, number>
    evidence_ids: string[]
    basis: string
    period_end: string
  }[]
  missing: string[]
  note?: string | null
  qualitative?: string | null
  qualitative_evidence_ids?: string[]
}

export interface NewsAnalysis {
  generation_status?: string | null
  failed_stage?: string | null
  status: 'completed' | 'partial' | 'search_failed' | 'no_results' | 'not_executed'
  overall_assessment: 'positive' | 'neutral' | 'cautious' | 'mixed' | 'insufficient'
  confidence: 'low' | 'medium'
  as_of: string
  news_items: {
    source_id: string
    category: string
    summary: string
    excerpt?: string | null
    attribution: string
    publication_date?: string | null
    publication_basis: string
    event_date?: string | null
    publisher_excerpt_matched: boolean
    related_source_ids: string[]
  }[]
  insights: { kind: string; text: string; evidence_ids: string[] }[]
  limitations: string[]
}

export interface InvestmentNarrative {
  language: 'en' | 'zh'
  sections: {
    topic: string
    text: string
    evidence_ids: string[]
    review_verdict: string
    review_reason?: string | null
    suggested_revision?: string | null
  }[]
}

export interface Report {
  investment_narrative?: InvestmentNarrative | null
  news_analysis?: NewsAnalysis | null
  valuation_status?: ValuationStatus | null
  valuations?: IssuerValuation[]
  research_quality?: { core_gaps: string[]; supplemental_gaps: string[]; historical_conflicts: number } | null
  investment_draft?: InvestmentAnalysis | null
  investment_review?: {
    generation: string
    review: string
    opinion: string
    verdicts: Record<string, string>
    reasons?: Record<string, string>
    batch_status: Record<string, string>
  } | null
  provider_events?: ProviderEvent[]
  investment_analysis?: InvestmentAnalysis | null
  input_report_ids?: string[]
  report_id: string
  task_id: string
  query: string
  generated_at: string
  data_as_of: string | null
  data_mode: DataMode
  providers?: Record<string, ProviderActivity>
  companies: Company[]
  metrics: Metric[]
  claims: Claim[]
  sources: Source[]
  limitations: string[]
  status: TaskStatus
}

/** Backend task object returned by POST /api/research and GET /api/research/{task_id}. */
export interface ResearchUsage {
  model: string | null
  prompt_tokens: number
  completion_tokens: number
  estimated_cost_usd: number | null
}

export interface TaskInfo {
  resource_usage?: ResearchUsage
  task_id: string
  status: TaskStatus
  progress: number
  current_step: string
  created_at: string
  updated_at: string
  report_id: string | null
  error_code: string | null
  error_message: string | null
}

export interface ResearchRequest {
  report_ids?: string[]
  query: string
  companies: string[]
  as_of_date: string | null
}

export interface CreateTaskResponse {
  task_id: string
  status: TaskStatus
}

export interface HealthInfo {
  provider?: string
  model?: string | null
  status: string
  mode: string
}

export interface CompaniesResponse {
  companies: string[]
}

/** Entry kept client-side (localStorage) for the "recent research" list. */
export interface RecentTaskEntry {
  task_id: string
  report_id: string | null
  query: string
  companies: string[]
  status: TaskStatus
  created_at: string
}

export type SettingsField = 'nebius_api_key' | 'tavily_api_key' | 'sec_contact_email'
export type StartupSettingsSource = 'environment' | 'dotenv'
export type SettingsSource = StartupSettingsSource | 'local_settings' | 'runtime'

export interface SettingsStatus {
  settings: Record<SettingsField, boolean>
  saved_keys: Record<SettingsField, boolean>
  effective_sources: Record<SettingsField, SettingsSource | null>
  startup_overrides: Record<SettingsField, StartupSettingsSource | null>
}

export type ConnectionCheck = 'not_configured' | 'reachable' | 'unavailable'
export type ConnectionChecks = Record<'nebius' | 'tavily', ConnectionCheck>

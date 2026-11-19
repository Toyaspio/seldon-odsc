/**
 * Metric grouping and comparability rules.
 * Metrics from different units, currencies or periods are never plotted on
 * one axis; the UI surfaces why they are not comparable instead.
 */
import type { Company, Metric, Report } from '../types/api'

export interface MetricGroup {
  metric_name: string
  metrics: Metric[]
  /** True when every metric in the group shares unit, currency and period, so one axis is honest. */
  comparable: boolean
  units: string[]
  currencies: (string | null)[]
  periods: string[]
  fiscalPeriods: string[]
  bases: string[]
  kinds: string[]
  capexScopes: (string | null)[]
  comparisonKey?: string
  comparisonIssues?: string[]
}

export function groupMetrics(metrics: Metric[]): MetricGroup[] {
  const byName = new Map<string, Metric[]>()
  for (const m of metrics) {
    const list = byName.get(m.metric_name) ?? []
    list.push(m)
    byName.set(m.metric_name, list)
  }
  const groups: MetricGroup[] = []
  for (const [metric_name, list] of byName) {
    const units = [...new Set(list.map((m) => m.unit))]
    const currencies = [...new Set(list.map((m) => m.currency))]
    const periods = [...new Set(list.map((m) => `${m.period_start ?? '?'}|${m.period_end}`))]
    const fiscalPeriods = [...new Set(list.map((m) => m.fiscal_period))]
    const bases = [...new Set(list.map((m) => m.accounting_basis))]
    const kinds = [...new Set(list.map((m) => m.actual_or_guidance))]
    const capexScopes = [...new Set(list.map((m) => m.capex_scope ?? null))]
    groups.push({
      metric_name,
      metrics: list,
      comparable: units.length === 1 && currencies.length === 1 && periods.length === 1 && fiscalPeriods.length === 1 && bases.length === 1 && kinds.length === 1 && capexScopes.length === 1,
      units,
      currencies,
      periods,
      fiscalPeriods,
      bases,
      kinds,
      capexScopes,
    })
  }
  return groups.sort((a, b) => a.metric_name.localeCompare(b.metric_name))
}

function reportingScope(metric: Metric): string {
  return JSON.stringify([metric.period_start, metric.period_end, metric.fiscal_period,
    metric.unit, metric.currency, metric.accounting_basis, metric.actual_or_guidance, metric.capex_scope ?? null])
}

export function observationKey(metric: Metric): string {
  return JSON.stringify([metric.company_id, metric.metric_name, reportingScope(metric), metric.value])
}

function reportingDifferences(group: MetricGroup): string[] {
  const issues: string[] = []
  if (group.periods.length > 1) issues.push('Reporting dates differ.')
  if (group.fiscalPeriods.length > 1) issues.push('Fiscal period labels differ.')
  if (group.units.length > 1) issues.push('Measurement units differ.')
  if (group.currencies.length > 1) issues.push('Currencies differ.')
  if (group.bases.length > 1) issues.push('Accounting bases differ.')
  if (group.kinds.length > 1) issues.push('Actuals, guidance or estimate types differ.')
  if (group.capexScopes.length > 1) issues.push('Cash CapEx asset scopes differ or are unknown.')
  return issues
}

function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === value
}

function comparisonGroups(group: MetricGroup): MetricGroup[] {
  const differences = reportingDifferences(group)
  const byScope = new Map<string, Metric[]>()
  for (const metric of group.metrics) {
    const scope = reportingScope(metric)
    const rows = byScope.get(scope) ?? []
    rows.push(metric)
    byScope.set(scope, rows)
  }

  const charts: MetricGroup[] = []
  const unmatched: Metric[] = []
  const unmatchedIssues = new Set(differences)
  for (const [scope, rows] of byScope) {
    const issues: string[] = []
    const first = rows[0]
    if (!validDate(first.period_end) || !first.fiscal_period || !first.unit || !first.accounting_basis
      || (first.period_start
        ? !validDate(first.period_start) || first.period_start > first.period_end
        : !/^as of /i.test(first.fiscal_period))) {
      issues.push('At least one record has missing or invalid period or accounting detail.')
    }
    if (new Set(rows.map((row) => row.company_id)).size < rows.length) {
      issues.push('The same company and reporting scope have conflicting values.')
    }
    if (rows.length < 2) issues.push('Some records have no matching observation.')
    if (issues.length > 0) {
      unmatched.push(...rows)
      issues.forEach((issue) => unmatchedIssues.add(issue))
    } else {
      const [matched] = groupMetrics(rows)
      charts.push({ ...matched, comparisonKey: JSON.stringify([group.metric_name, scope]),
        comparisonIssues: differences })
    }
  }
  if (unmatched.length > 0) {
    const [remaining] = groupMetrics(unmatched)
    charts.push({ ...remaining, comparable: false, comparisonKey: JSON.stringify([group.metric_name]),
      comparisonIssues: [...unmatchedIssues] })
  }
  return charts
}

export function compareReports(reports: Report[]): { companies: Company[]; groups: MetricGroup[] } {
  const companies = new Map<string, Company>()
  const records = new Map<string, Metric>()
  const ordered = [...reports].sort((a, b) => a.generated_at.localeCompare(b.generated_at)
    || a.report_id.localeCompare(b.report_id))

  for (const report of ordered) {
    for (const company of report.companies) companies.set(company.company_id, company)
    for (const metric of report.metrics) {
      const key = observationKey(metric)
      const previous = records.get(key)
      const representative = previous && previous.metric_id <= metric.metric_id ? previous : metric
      records.set(key, {
        ...representative,
        evidence_ids: [...new Set([...(previous?.evidence_ids ?? []), ...metric.evidence_ids])].sort(),
      })
    }
  }

  const metrics = [...records].sort(([a], [b]) => a.localeCompare(b)).map(([, metric]) => metric)
  return {
    companies: [...companies.values()].sort((a, b) => a.company_id.localeCompare(b.company_id)),
    groups: groupMetrics(metrics).flatMap(comparisonGroups),
  }
}

export function groupLabel(group: MetricGroup): string {
  const name = group.metric_name
  const label = name
    .replace(/^(synthetic_|mock_)/, '')
    .split(/[_\s]+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ')
  const [scope] = group.capexScopes
  return group.capexScopes.length === 1 && scope ? label + ' · ' + capexScopeLabel(scope) : label
}

export function capexScopeLabel(scope: string): string {
  const labels: Record<string, string> = {
    ppe: 'PP&E',
    productive_assets: 'PP&E and intangibles',
    ppe_and_land_use: 'PP&E and land-use rights',
    ppe_excluding_campus_land: 'PP&E excluding land rights and campus construction',
    ppe_excluding_leased_equipment: 'PP&E excluding equipment leased to others',
    other_long_term_investing_assets: 'Consolidated investing cash-flow CapEx',
  }
  return labels[scope] ?? scope.replaceAll('_', ' ')
}

export function companyById(companies: Company[]): Map<string, Company> {
  return new Map(companies.map((c) => [c.company_id, c]))
}

/** Panel section hints grounded only in metric names present in the report. */
export function metricsMatching(metrics: Metric[], keywords: string[]): Metric[] {
  return metrics.filter((m) => keywords.some((k) => m.metric_name.toLowerCase().includes(k)))
}

export const CHART_SERIES_COLORS = ['#2dd4bf', '#6aa6f8', '#a78bfa', '#f5a623']


export const REPORT_PERIODS = ['Annual', 'Quarterly', 'YTD', 'TTM',
  'Company guidance', 'Market estimates', 'Model inference', 'Other periods'] as const

export function reportPeriod(metric: Metric): typeof REPORT_PERIODS[number] {
  if (metric.actual_or_guidance === 'guidance') return 'Company guidance'
  if (metric.actual_or_guidance === 'analyst_estimate') return 'Market estimates'
  if (metric.actual_or_guidance === 'model_inference') return 'Model inference'
  if (!metric.period_start || !validDate(metric.period_start) || !validDate(metric.period_end)
      || metric.period_start > metric.period_end) return 'Other periods'
  if (/\b(ttm|ltm|trailing)\b/i.test(metric.fiscal_period)) return 'TTM'
  if (metric.duration_kind === 'year') return 'Annual'
  if (metric.duration_kind === 'quarter') return 'Quarterly'
  if (metric.duration_kind === 'ytd') return 'YTD'
  const days = (Date.parse(metric.period_end) - Date.parse(metric.period_start)) / 86400000 + 1
  if (days >= 80 && days <= 100) return 'Quarterly'
  if (days >= 160 && days <= 300) return 'YTD'
  if (days >= 360 && days <= 371 && /(^FY|annual|year)/i.test(metric.fiscal_period)) return 'Annual'
  return 'Other periods'
}

export function reportFinancials(metrics: Metric[]) {
  const buckets = new Map<string, Metric[]>()
  for (const metric of metrics) {
    const period = reportPeriod(metric)
    const days = metric.period_start ? (Date.parse(metric.period_end) - Date.parse(metric.period_start)) / 86400000 + 1 : 0
    const key = JSON.stringify([period, metric.company_id, metric.metric_name, metric.unit,
      metric.currency, metric.accounting_basis, metric.actual_or_guidance, metric.capex_scope ?? null,
      period === 'YTD' ? Math.round(days / 91) : null])
    const rows = buckets.get(key) ?? []
    rows.push(metric)
    buckets.set(key, rows)
  }
  const categories = REPORT_PERIODS.map((period) => ({ period, latest: [] as Metric[], history: [] as Metric[] }))
  const trends: Metric[][] = []
  for (const rows of buckets.values()) {
    rows.sort((a, b) => ((validDate(b.period_end) ? Date.parse(b.period_end) : 0)
      - (validDate(a.period_end) ? Date.parse(a.period_end) : 0)) || a.metric_id.localeCompare(b.metric_id))
    const first = rows[0]
    const period = reportPeriod(first)
    const category = categories.find((item) => item.period === period)!
    category.latest.push(...rows.filter((row) => row.period_end === first.period_end))
    category.history.push(...rows.filter((row) => row.period_end !== first.period_end))
    const observations = new Map<string, Metric>()
    let conflict = false
    for (const row of rows) {
      const previous = observations.get(row.period_end)
      if (previous && (previous.value !== row.value || previous.period_start !== row.period_start)) conflict = true
      observations.set(row.period_end, row)
    }
    if (['Annual', 'Quarterly', 'YTD', 'TTM'].includes(period) && !conflict && observations.size >= 2
        && first.unit && first.accounting_basis
        && (!['cash_capex', 'free_cash_flow'].some((name) => first.metric_name.startsWith(name)) || first.capex_scope)) {
      trends.push([...observations.values()].reverse())
    }
  }
  const recency = (a: Metric, b: Metric) =>
    (validDate(b.period_end) ? Date.parse(b.period_end) : 0) - (validDate(a.period_end) ? Date.parse(a.period_end) : 0)
  for (const category of categories) {
    const dates = new Map<string, string>()
    const key = (metric: Metric) => JSON.stringify([metric.company_id, metric.metric_name])
    category.latest.sort(recency)
    category.latest = category.latest.filter((metric) => {
      const latest = dates.get(key(metric))
      if (!latest) dates.set(key(metric), metric.period_end)
      if (!latest || latest === metric.period_end) return true
      category.history.push(metric)
      return false
    })
    category.history.sort(recency)
  }
  return { categories: categories.filter((item) => item.latest.length), trends }
}

import { deepEqual, equal, match } from 'node:assert/strict'
import { test } from 'node:test'
import { compareReports } from '../src/lib/metrics.ts'

const annual = {
  metric_id: 'annual', company_id: 'a', metric_name: 'cash_capex', value: 100,
  period_start: '2025-01-01', period_end: '2025-12-31', fiscal_period: 'FY2025',
  currency: 'USD', unit: 'USD', accounting_basis: 'US GAAP', actual_or_guidance: 'actual',
  evidence_ids: ['s1'],
}
const quarter = {
  ...annual, metric_id: 'quarter', period_start: '2025-10-01', fiscal_period: 'Q4 2025', value: 27,
}
const ytd = {
  ...annual, metric_id: 'ytd', period_end: '2025-09-30', fiscal_period: '9M 2025', value: 73,
}
const report = (id, metrics) => ({
  report_id: id, generated_at: '2026-01-01T00:00:00Z', companies: [], metrics,
})

for (const [label, records, reason] of [
  ['annual and quarterly', [annual, quarter], /Reporting dates differ/],
  ['annual and YTD', [annual, ytd], /Reporting dates differ/],
  ['multiple quarters', [quarter, { ...quarter, metric_id: 'q3', period_start: '2025-07-01',
    period_end: '2025-09-30', fiscal_period: 'Q3 2025' }], /Reporting dates differ/],
  ['different currencies', [annual, { ...annual, currency: 'EUR' }], /Currencies differ/],
  ['different accounting bases', [annual, { ...annual, accounting_basis: 'IFRS' }], /Accounting bases differ/],
  ['actual and guidance', [annual, { ...annual, actual_or_guidance: 'guidance' }], /guidance/],
  ['conflicting values for the same period', [annual, { ...annual, value: 101 }], /conflicting values/],
]) {
  test(label + ' are retained and not plotted together', () => {
    const { groups } = compareReports(records.map((m, i) => report(String(i), [m])))
    equal(groups.length, 1)
    equal(groups[0].metrics.length, records.length)
    equal(groups[0].comparable, false)
    match(groups[0].comparisonIssues.join(' '), reason)
  })
}

test('one chart per truly matching scope; unmatched periods remain visible', () => {
  const result = compareReports([report('a', [annual, quarter, ytd]), report('b', [
    { ...annual, company_id: 'b', metric_id: 'b-year' },
    { ...quarter, company_id: 'b', metric_id: 'b-q4' },
  ])])
  const charts = result.groups.filter((g) => g.comparable)
  equal(charts.length, 2)
  equal(result.groups.flatMap((g) => g.metrics).length, 5)
  equal(result.groups.find((g) => !g.comparable).metrics[0].metric_id, ytd.metric_id)
})

test('reordering reports and rows produces an identical result', () => {
  const reissued = { ...annual, metric_id: 'a-copy', evidence_ids: ['s2'] }
  const a = report('one', [annual, quarter])
  const b = report('two', [ytd, reissued])
  const ordered = compareReports([a, b])
  const reversed = compareReports([b, a].map((r) => ({ ...r, metrics: [...r.metrics].reverse() })))
  deepEqual(ordered, reversed)
  equal(ordered.groups.flatMap((g) => g.metrics).length, 3)
  deepEqual(ordered.groups[0].metrics.find((m) => m.fiscal_period === 'FY2025').evidence_ids, ['s1', 's2'])
})

test('invalid or missing period boundaries cannot create a chart', () => {
  for (const period of [{ period_start: null }, { period_end: '2025-02-30' }]) {
    const a = { ...annual, ...period }
    const { groups } = compareReports([report('a', [a]), report('b', [{ ...a, company_id: 'b' }])])
    equal(groups[0].comparable, false)
    match(groups[0].comparisonIssues.join(' '), /missing or invalid period/)
  }
})

test('matching periods with different CapEx asset scopes remain separate', () => {
  const result = compareReports([
    report('a', [{ ...annual, capex_scope: 'ppe' }]),
    report('b', [{ ...annual, company_id: 'b', metric_id: 'b-scope', capex_scope: 'productive_assets' }]),
  ])
  equal(result.groups.length, 1)
  equal(result.groups[0].comparable, false)
  match(result.groups[0].comparisonIssues.join(' '), /asset scopes/)
})

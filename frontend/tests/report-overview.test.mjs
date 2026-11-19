import test from 'node:test'
import assert from 'node:assert/strict'
import { reportFinancials, reportPeriod } from '../src/lib/metrics.ts'

const base = {company_id: 'nvda', metric_name: 'cash_capex', value: 10, currency: 'USD',
  unit: 'USD', fiscal_period: 'FY', accounting_basis: 'US GAAP', actual_or_guidance: 'actual',
  capex_scope: 'ppe', evidence_ids: []}
const row = (metric_id, period_start, period_end, extras = {}) =>
  ({...base, metric_id, period_start, period_end, ...extras})

test('latest annual, quarter, YTD and trailing coverage precede history', () => {
  const annual = row('current', '2025-02-01', '2026-01-31')
  const previous = row('previous', '2024-02-01', '2025-01-31')
  const quarter = row('q', '2026-02-01', '2026-04-30', {fiscal_period: 'Q1'})
  const ytd = row('y', '2026-02-01', '2026-07-31', {fiscal_period: 'Q2'})
  const ttm = row('t', '2025-08-01', '2026-07-31', {fiscal_period: 'TTM'})
  const result = reportFinancials([previous, ytd, ttm, quarter, annual])
  assert.deepEqual(result.categories.map(({period}) => period), ['Annual', 'Quarterly', 'YTD', 'TTM'])
  assert.equal(result.categories[0].latest[0].metric_id, 'current')
  assert.equal(result.categories[0].history[0].metric_id, 'previous')
  assert.deepEqual(result.trends.map(rows => rows.map(m => m.metric_id)), [['previous', 'current']])
})

test('asset scope, currency, basis, guidance and conflicting values never mix in a trend', () => {
  const start = '2025-02-01', end = '2026-01-31'
  const rows = [row('base', start, end), row('conflict', start, end, {value: 99}),
    row('prior', '2024-02-01', '2025-01-31'), row('eur', start, end, {currency: 'EUR'}),
    row('scope', start, end, {capex_scope: 'productive_assets'}),
    row('basis', start, end, {accounting_basis: 'IFRS'}),
    row('guide', start, end, {actual_or_guidance: 'guidance'}),
    row('estimate', start, end, {actual_or_guidance: 'analyst_estimate'})]
  const result = reportFinancials(rows)
  assert.equal(result.trends.length, 0)
  assert.equal(result.categories.flatMap(c => [...c.latest, ...c.history]).length, rows.length)
  assert.equal(reportPeriod(rows.at(-2)), 'Company guidance')
  assert.equal(reportPeriod(rows.at(-1)), 'Market estimates')
})

test('a retired asset scope is history when the same metric has a later valid period', () => {
  const recent = row('recent', '2025-02-01', '2026-01-31', {capex_scope: 'productive_assets'})
  const old = row('old', '2019-02-01', '2020-01-31')
  const [annual] = reportFinancials([old, recent]).categories
  assert.deepEqual(annual.latest.map(item => item.metric_id), ['recent'])
  assert.deepEqual(annual.history.map(item => item.metric_id), ['old'])
})

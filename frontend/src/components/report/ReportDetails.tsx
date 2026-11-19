import { useState, type ReactNode } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { InvestmentAnalysis, Metric, Report, ResearchUsage, Source } from '../../types/api'
import { capexScopeLabel, reportPeriod } from '../../lib/metrics'
import { formatValue } from '../../lib/format'

const providerLabels: Record<string, string> = {
  nebius: 'Nebius', tavily: 'Tavily', sec: 'SEC EDGAR', esef: 'Issuer ESEF',
}

const stanceLabels = {
  bullish: 'Bullish', cautiously_bullish: 'Cautiously Bullish', neutral: 'Neutral',
  cautiously_bearish: 'Cautiously Bearish', bearish: 'Bearish', provisional_neutral: 'Conditional outlook', insufficient: 'Conditional outlook',
}

const topicLabels: Record<string, string> = {
  thesis: 'Investment thesis', operating_state: 'Current operating performance',
  supporting_case: 'Bull argument', opposing_case: 'Bear argument', tradeoff: 'Evidence trade-off',
  market_expectations: 'Fundamentals and market expectations', key_factor: 'Key factor',
  bull_case: 'Bull scenario', base_case: 'Base scenario', bear_case: 'Bear scenario',
  invalidation: 'What would change the view', horizon: 'Observation horizon',
  financial_quality: 'Earnings and cash flow quality', outlook: 'Operating outlook',
  risk: 'Key risk', counterargument: 'Counterargument', catalyst: 'Catalyst', valuation: 'Valuation and price',
}

const readable = (value: string) => value.replaceAll('_', ' ')

function PointQualification({ report, point }: { report: Report; point: InvestmentAnalysis['points'][number] }) {
  const section = report.investment_narrative?.sections.find((item) =>
    item.topic === point.topic && item.text === point.text)
  if (!section || !['partially_supported', 'needs_qualification'].includes(section.review_verdict)) return null
  return <p className="text-sm text-fog-300">Qualified model assessment · {section.suggested_revision || section.review_reason}</p>
}

export function ReportDisclosure({ title, children, initiallyOpen = false }:
  { title: string; children: ReactNode; initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen)
  return <details className="panel" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary className="cursor-pointer px-5 py-4 text-sm font-semibold text-fog-200">{title}</summary>
    {open && <div className="space-y-4 border-t border-ink-700/60 p-5">{children}</div>}
  </details>
}

export function InvestmentHeadline({ report }: { report: Report }) {
  const view = report.investment_analysis
  if (!view) return null
  const hasDirection = !['insufficient', 'provisional_neutral'].includes(view.stance)
  const comparative = Boolean(report.input_report_ids?.length)
  const thesis = view.points.find((point) => point.topic === 'thesis')
  const outlook = view.points.find((point) => ['base_case', 'outlook', 'bull_case', 'bear_case'].includes(point.topic))
  const primary = thesis ?? outlook ?? view.points[0]
  const risk = view.points.find((point) => ['counterargument', 'opposing_case', 'risk'].includes(point.topic))
  const trigger = view.points.find((point) => point.topic === 'invalidation')
  if (!comparative && !hasDirection && !primary) return null
  return <section className="panel space-y-3 border-l-2 border-l-signal-500 p-5" aria-label="Investment analysis">
    <h2 className="text-sm font-semibold text-fog-400">{comparative ? 'Comparative assessment' : 'Investment View'}</h2>
    {report.investment_review?.opinion === 'provisional' &&
      <p className="text-xs text-fog-400">Provisional view · Review is incomplete or the view requires qualification.</p>}
    {!comparative && <p className="text-2xl font-semibold text-fog-100">{hasDirection ? stanceLabels[view.stance] : 'Conditional outlook'}</p>}
    {comparative && primary && <p className="leading-relaxed text-fog-100">{primary.text}</p>}
    {view.observation_reason && <p className="leading-relaxed text-fog-100">{view.observation_reason}</p>}
    {!comparative && primary && <p className="leading-relaxed text-fog-100">{primary.text}</p>}
    {primary && <PointQualification report={report} point={primary} />}
    {comparative && !primary && <p className="text-sm text-fog-400">The comparative answer could not be generated. The financial findings are available below.</p>}
    {risk && <p className="text-sm text-fog-300"><strong>Key risk</strong> · {risk.text}</p>}
    {risk && <PointQualification report={report} point={risk} />}
    {trigger && <p className="text-sm text-fog-300"><strong>What would change the view</strong> · {trigger.text}</p>}
    {trigger && <PointQualification report={report} point={trigger} />}
    {view.missing_inputs[0] && <p className="text-sm text-fog-400">{view.missing_inputs[0].replace(/\s*\((?:web|sec_xbrl|sec_statement):[a-z0-9]+\)/gi, '')}</p>}
  </section>
}

function Citation({ source }: { source: Source }) {
  const kind = source.source_type === 'issuer_lead' ? 'Issuer publication · unverified excerpt'
    : ['web_search', 'regulator_lead'].includes(source.source_type) ? 'External lead · unverified excerpt'
    : 'Report evidence'
  const label = <>{source.publisher} · {source.title} <span className="text-fog-500">({kind})</span></>
  return /^https?:\/\//i.test(source.url)
    ? <a className="text-info-400 underline" href={source.url} target="_blank" rel="noreferrer">{label}</a>
    : <span>{label}</span>
}

export function InvestmentArticle({ report }: { report: Report }) {
  const sources = new Map(report.sources.map((source) => [source.source_id, source]))
  const sections = report.investment_narrative?.sections.filter((section) =>
    section.review_verdict === 'supported' ||
    (['partially_supported', 'needs_qualification'].includes(section.review_verdict) &&
      Boolean(section.suggested_revision || section.review_reason) && section.evidence_ids.length > 0) ||
    (section.review_verdict === 'pending' && section.evidence_ids.length > 0 &&
      section.evidence_ids.every((id) => sources.has(id)))
  ) ?? []
  if (!sections.length) return null
  return <ReportDisclosure title="Full Investment Analysis" initiallyOpen>
    <div className="mx-auto max-w-4xl space-y-5">
      {sections.map((section, index) => (
        <section key={index} className="space-y-1">
          <h3 className="font-semibold text-fog-200">{topicLabels[section.topic] ?? readable(section.topic)}</h3>
          {['partially_supported', 'needs_qualification'].includes(section.review_verdict) &&
            <p className="text-sm text-fog-300">{['bull_case', 'base_case', 'bear_case'].includes(section.topic)
              ? 'Scenario assumptions' : 'Qualified model assessment'} · {section.suggested_revision || section.review_reason}</p>}
          {section.review_verdict === 'pending' && <p className="text-xs text-fog-400">The cited premises still need confirmation.</p>}
          <p className="whitespace-pre-line leading-relaxed text-fog-100">{section.text}</p>
          <ul className="text-xs text-fog-400" aria-label="Paragraph citations">
            {[...new Set(section.evidence_ids)].map((id) => {
              const source = sources.get(id)
              return source ? <li key={id}><Citation source={source} /></li> : null
            })}
          </ul>
        </section>
      ))}
    </div>
  </ReportDisclosure>
}

export function InvestmentDetail({ report }: { report: Report }) {
  const points = report.investment_analysis?.points ?? []
  if (!points.length) return null
  const sources = new Map(report.sources.map((source) => [source.source_id, source]))
  let rank = 0
  return <ReportDisclosure title="Independent investment analysis" initiallyOpen>
    <p className="text-xs text-fog-400">Agent judgments and conditional scenarios. See Research claims &amp; verification for the status of reported facts.</p>
    {points.map((point, index) => <article key={index} className="space-y-2 border-b border-ink-700/60 pb-3">
      <h3 className="font-semibold capitalize">{point.topic === 'key_factor' ? `Key factor ${++rank}`
        : topicLabels[point.topic] ?? readable(point.topic)}</h3>
      <p className="text-sm leading-relaxed text-fog-200">{point.text}</p>
      <PointQualification report={report} point={point} />
      <ul className="space-y-1 text-xs">{point.evidence_ids.map((id) => {
        const source = sources.get(id)
        return source ? <li key={id}><Citation source={source} /></li> : null
      })}</ul>
    </article>)}
  </ReportDisclosure>
}

export function ProviderDetails({ report, usage }: { report: Report; usage?: ResearchUsage }) {
  const events = report.provider_events ?? []
  const sources = new Map(report.sources.map((source) => [source.source_id, source]))
  const issuers = new Map(report.companies.map((company) => [company.company_id, company.company_name]))
  const active = Object.entries(report.providers ?? {}).filter(([, item]) => item.calls > 0 || item.mock_used)
  return <section className="panel space-y-4 p-5" aria-label="Provider Activity">
    <h2 className="font-semibold">Provider Activity</h2>
    {report.investment_review && <p className="text-sm text-fog-400">
      {Object.entries(report.investment_review.batch_status).map(([id, state]) =>
        <span className="mr-3 inline-block" key={id}>{id} · {readable(state)}</span>)}
    </p>}
    {usage?.model && <p className="text-xs text-fog-400">{usage.model} · {usage.prompt_tokens.toLocaleString()} input /
      {' '}{usage.completion_tokens.toLocaleString()} output tokens
      {usage.estimated_cost_usd != null && <> · Estimated model cost USD {usage.estimated_cost_usd.toFixed(6)}</>}
    </p>}
    {!active.length && <p className="text-sm text-fog-400">No provider operations recorded.</p>}
    {active.map(([name, item]) => {
      const steps = events.filter((event) => event.provider === name)
      const sum = (key: 'elapsed_ms' | 'prompt_tokens' | 'completion_tokens') =>
        steps.reduce((total, step) => total + (step[key] ?? 0), 0)
      return <details key={name} className="rounded-lg border border-ink-700 px-3 py-2">
        <summary className="cursor-pointer text-sm">
          <strong>{providerLabels[name] ?? name}</strong> · {item.calls} operations · {item.succeeded} succeeded · {item.failed} failed
          {item.http_attempts != null && <> · {item.http_attempts} HTTP attempts · {item.http_responses ?? 0} responses</>}
          {item.mock_used && ' · Mock'}
        </summary>
        <div className="mt-3 space-y-3 text-sm text-fog-300">
          {!steps.length && <p>Per-operation details were not recorded for this report.</p>}
          {!!steps.length && <p>Total operation time {(sum('elapsed_ms') / 1000).toFixed(2)} s
            {steps.some((step) => step.prompt_tokens != null || step.completion_tokens != null) &&
              <> · {sum('prompt_tokens')} input / {sum('completion_tokens')} output tokens</>}
          </p>}
          {steps.map((event, index) => <article key={index} className="space-y-1 border-t border-ink-700 pt-2">
            <p className="capitalize">{readable(event.stage)} · {event.outcome} · {event.elapsed_ms} ms</p>
            <p>{event.http_attempts} HTTP attempts · {event.http_responses} responses
              {event.error_code && <> · {event.error_code}</>}{event.model && <> · {event.model}</>}</p>
            {(event.batch_id || (event.provider === 'nebius' && event.stage.startsWith('news_'))) && <p>
              {event.batch_id ? 'Batch ' + event.batch_id : event.stage.endsWith('_retry') ? 'Retry attempt' : 'Initial attempt'}
              {event.prompt_tokens != null && <> · {event.prompt_tokens} input tokens</>}
              {event.completion_tokens != null && <> · {event.completion_tokens} output tokens</>}
              {event.max_output_tokens != null && <> · cap {event.max_output_tokens}</>}
              {event.finish_reason && <> · finish {event.finish_reason}</>}
              {event.http_status != null && <> · HTTP {event.http_status}</>}
              {!!event.retry_wait_seconds && <> · retry wait {event.retry_wait_seconds.toFixed(2)} s</>}
            </p>}
            {event.company_id && <p>{issuers.get(event.company_id) ?? event.company_id}</p>}
            {event.topic && <p className="font-medium">{event.topic}</p>}
            {event.query && <p className="break-words">{event.query}</p>}
            {event.result_count != null && <p>{event.result_count} results returned
              {event.usable_results != null && <> · {event.usable_results} usable</>}
              <> · {event.adopted_source_ids?.length ?? 0} cited in the report</></p>}
            {event.provider === 'tavily' && event.stage.startsWith('news_') && (
              <p>{event.news_context_source_ids?.length ?? 0} sent to news analysis · {' '}
                {event.news_selected_source_ids?.length ?? 0} selected · {' '}
                {event.news_cited_source_ids?.length ?? 0} cited in news conclusions</p>
            )}
            {!!event.adopted_source_ids?.length && <ul>{event.adopted_source_ids.map((id) => {
              const source = sources.get(id)
              return source ? <li key={id}><Citation source={source} /></li> : null
            })}</ul>}
            {event.filings_retrieved != null && <p>{event.filings_retrieved} filing documents downloaded
              {event.filings_parsed != null && <> · {event.filings_parsed} parsed into financial records</>}
              {event.filing_parse_failures != null && <> · {event.filing_parse_failures} could not be parsed into financial records</>}
              {!!event.filing_types?.length && <> · Forms referenced {event.filing_types.join(', ')}</>}</p>}
            {event.parsed != null && <p>Company Facts parsed · {event.parsed ? 'Yes' : 'No'}
              {event.extracted != null && <> · {event.extracted} metrics extracted</>}
              {event.verified != null && <> · {event.verified} metrics verified</>}</p>}
          </article>)}
        </div>
      </details>
    })}
  </section>
}

export function FinancialTrend({ rows, company }: { rows: Metric[]; company: string }) {
  const first = rows[0]
  return <section className="space-y-2">
    <h3 className="text-sm font-semibold">{company} · {readable(first.metric_name)} · {reportPeriod(first)}</h3>
    <p className="text-xs text-fog-400">{first.unit} · {first.accounting_basis}
      {first.capex_scope && <> · {capexScopeLabel(first.capex_scope)}</>}</p>
    <div className="h-48">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows}>
          <CartesianGrid stroke="#212939" />
          <XAxis dataKey="period_end" tick={{ fill: '#99a6bc', fontSize: 10 }} />
          <YAxis tickFormatter={formatValue} tick={{ fill: '#99a6bc', fontSize: 10 }} />
          <Tooltip />
          <Line dataKey="value" stroke="#2dd4bf" dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  </section>
}

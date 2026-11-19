import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { FileSearch, ScrollText } from 'lucide-react'
import type { Metric, Report, ResearchUsage } from '../../types/api'
import { companyById, reportFinancials } from '../../lib/metrics'
import { formatDateTime } from '../../lib/format'
import { DataModeBadge, ResolutionBadge, StatusBadge } from '../ui/Badges'
import { EmptyState } from '../ui/EmptyState'
import { Panel } from '../ui/Panel'
import { FailedBanner, MockBanner, PartialBanner } from './ReportBanners'
import { FinancialMetricCard } from './FinancialMetricCard'
import { ClaimCard } from './ClaimCard'
import {
  EvidenceInspector,
  claimToTarget,
  metricToTarget,
  type EvidenceTarget,
} from './EvidenceInspector'
import { SourceList } from './SourceList'
import { LimitationsPanel } from './LimitationsPanel'

import { NewsIntelligence } from './NewsIntelligence'
import { FinancialTrend, InvestmentArticle, InvestmentDetail, InvestmentHeadline, ProviderDetails, ReportDisclosure } from './ReportDetails'

function metricEvidenceTitles(metric: Metric, report: Report): string[] {
  return report.sources
    .filter((s) => metric.evidence_ids.includes(s.source_id))
    .map((s) => s.title)
}

/**
 * Full presentation of one report. Used by the report page (backend data)
 * and by the dev-only fixture preview (clearly-labeled static data).
 */
export function ReportView({ report, usage }: { report: Report; usage?: ResearchUsage }) {
  const [target, setTarget] = useState<EvidenceTarget | null>(null)

  const financials = useMemo(() => reportFinancials(report.metrics), [report.metrics])

  const companies = companyById(report.companies)
  const hasMetrics = report.metrics.length > 0
  function renderMetric(metric: Metric) {
    const name = companies.get(metric.company_id)?.company_name ?? metric.company_id
    return <FinancialMetricCard key={metric.metric_id} metric={metric} companyName={name}
      evidenceTitles={metricEvidenceTitles(metric, report)}
      onInspect={(selected) => setTarget(metricToTarget(selected, name))} />
  }

  const secSources = report.sources.filter((source) => source.source_type === 'sec_xbrl')
  const issuerSources = report.sources.filter((source) => source.source_type === 'esef_xbrl')
  const webSources = report.sources.filter((source) =>
    ['issuer_lead', 'regulator_lead', 'web_search'].includes(source.source_type))
  const otherSources = report.sources.filter((source) =>
    !['sec_xbrl', 'esef_xbrl', 'issuer_lead', 'regulator_lead', 'web_search'].includes(source.source_type))

  return (
    <div className="animate-fade-up space-y-6">
      {/* Header */}
      <header className="panel p-5">
        <div className="flex flex-wrap items-center gap-2">
          <DataModeBadge mode={report.data_mode} />
          <StatusBadge status={report.status} />
          {report.data_as_of && (
            <span className="num text-[11px] text-fog-500">Data as of {report.data_as_of}</span>
          )}
        </div>
        <h1 className="mt-3 text-lg font-semibold leading-relaxed text-fog-100">{report.query}</h1>
        <div className="mt-3 flex flex-wrap gap-2">
          {report.companies.map((c) => (
            <span
              key={c.company_id}
              className="inline-flex items-center gap-2 rounded-full border border-ink-700 bg-ink-900 py-1 pl-3 pr-2 text-xs text-fog-300"
            >
              {c.company_name}
              {c.ticker && <span className="num text-fog-600">{c.ticker}</span>}
              <ResolutionBadge status={c.resolution_status} />
            </span>
          ))}
        </div>
      </header>

      {/* Mode & status banners */}
      {report.data_mode === 'mock' && <MockBanner />}
      {report.status === 'partial' && report.data_mode !== 'mock' && <PartialBanner reasons={report.research_quality?.core_gaps} />}
      {!!report.input_report_ids?.length && (
        <section className="panel space-y-2 p-5">
          <h2 className="font-medium">Input reports</h2>
          {report.input_report_ids.map((id) => <Link className="block underline" key={id} to={`/report/${id}`}>{id}</Link>)}
        </section>
      )}
      <InvestmentHeadline report={report} />
      <ProviderDetails report={report} usage={usage} />
      <InvestmentArticle report={report} />
      <NewsIntelligence report={report} />
      {report.status === 'failed' && <FailedBanner />}

      {/* Financial metrics */}
      <Panel
        title="Financial metrics"
        subtitle={
          hasMetrics
            ? 'Exactly as returned by the backend — values are never inferred or zero-filled.'
            : undefined
        }
      >
        {!hasMetrics ? (
          <EmptyState
            title="No financial metrics in this report"
            detail="The pipeline did not produce any structured financial records. Nothing is plotted, because there is nothing honest to plot."
            icon={<ScrollText size={18} />}
          />
        ) : (
          <div className="space-y-6">
            {financials.categories.map((category) => (
              <section key={category.period} aria-label={category.period} className="space-y-3">
                <h3 className="font-semibold">{category.period}</h3>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  {category.latest.map(renderMetric)}
                </div>
                {category.history.length > 0 && <ReportDisclosure title={`Historical ${category.period} records (${category.history.length})`}>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    {category.history.map(renderMetric)}
                  </div>
                </ReportDisclosure>}
              </section>
            ))}
          </div>
        )}
      </Panel>
      {financials.trends.length > 0 && <ReportDisclosure title="Comparable financial trends">
        <p className="text-sm text-fog-400">Each chart uses one issuer, reporting period type and financial scope. Other records remain in the financial sections.</p>
        {financials.trends.map((rows) => (
          <FinancialTrend key={rows[0].metric_id} rows={rows}
            company={companies.get(rows[0].company_id)?.company_name ?? rows[0].company_id} />
        ))}
      </ReportDisclosure>}
      {!report.investment_narrative?.sections.some((section) => section.review_verdict === 'supported') && <InvestmentDetail report={report} />}
      {report.research_quality && (report.research_quality.historical_conflicts > 0
        || report.research_quality.supplemental_gaps.length > 0) && (
        <ReportDisclosure title="Financial evidence quality">
          {report.research_quality.historical_conflicts > 0 && (
            <p>{report.research_quality.historical_conflicts} distinct historical or out-of-scope conflicting financial observations.
              The original source records remain available in Research claims &amp; verification.</p>
          )}
          <ul className="list-disc pl-5">
            {report.research_quality.supplemental_gaps.map((gap) => <li key={gap}>{gap}</li>)}
          </ul>
        </ReportDisclosure>
      )}
      {!!report.investment_analysis?.missing_inputs.length && (
        <ReportDisclosure title="Investment evidence gaps">
          <ul className="list-disc pl-5 text-sm text-fog-300">
            {report.investment_analysis.missing_inputs.map((gap, index) => <li key={index}>{gap}</li>)}
          </ul>
        </ReportDisclosure>
      )}

      {/* Claims & evidence */}
      <ReportDisclosure title="Research claims & verification">
        {report.claims.length === 0 ? (
          <EmptyState
            title="No claims were produced"
            detail="The agent did not emit any verifiable claims for this question. This is expected for some mock or live-partial runs."
            icon={<FileSearch size={18} />}
          />
        ) : (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {report.claims.map((claim) => (
              <ClaimCard
                key={claim.claim_id}
                claim={claim}
                sources={report.sources}
                companyName={
                  claim.company_id
                    ? (companies.get(claim.company_id)?.company_name ?? claim.company_id)
                    : null
                }
                onOpen={(c) => setTarget(claimToTarget(c, report.companies))}
              />
            ))}
          </div>
        )}
      </ReportDisclosure>

      {/* Sources */}
      <ReportDisclosure title="Sources">
        {report.sources.length === 0 ? (
          <EmptyState
            title="No sources recorded"
            detail="The backend returned an empty source list. Claims in this report therefore have no retrievable provenance."
          />
        ) : (
          <div className="space-y-4">
            {([
              ['SEC EDGAR filings', secSources],
              ['Issuer Inline XBRL filings', issuerSources],
              ['Web search leads', webSources],
              ['Other records', otherSources],
            ] as const).map(([title, sources]) => sources.length > 0 && (
              <section key={title} aria-label={title} className="space-y-2">
                <h3 className="text-sm font-semibold text-fog-300">{title}</h3>
                <SourceList sources={sources} />
              </section>
            ))}
          </div>
        )}
      </ReportDisclosure>

      <LimitationsPanel limitations={report.limitations} />

      <ReportDisclosure title="Technical details">
        <a className="mt-3 inline-block text-xs underline"
          href={`/api/reports/${encodeURIComponent(report.report_id)}`} download={`report-${report.report_id}.json`}>
          Download report JSON
        </a>
        <dl className="num mt-3 flex flex-wrap gap-x-6 gap-y-1 border-t border-ink-700/60 pt-3 text-[11px] text-fog-500">
          <div className="flex gap-1.5">
            <dt className="text-fog-600">Report ID</dt>
            <dd className="font-mono text-fog-400">{report.report_id}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-fog-600">Generated</dt>
            <dd className="text-fog-400">{formatDateTime(report.generated_at)}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-fog-600">Task</dt>
            <dd>
              <Link to={`/task/${report.task_id}`} className="font-mono text-info-400 hover:underline">
                {report.task_id}
              </Link>
            </dd>
          </div>
        </dl>

      </ReportDisclosure>

      <EvidenceInspector target={target} sources={report.sources} onClose={() => setTarget(null)} />
    </div>
  )
}

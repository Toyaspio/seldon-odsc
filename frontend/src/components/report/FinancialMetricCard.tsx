import type { Metric } from '../../types/api'
import { formatFullValue } from '../../lib/format'
import { capexScopeLabel } from '../../lib/metrics'
import { ActualOrGuidanceBadge, VerificationDot } from '../ui/Badges'

interface FinancialMetricCardProps {
  metric: Metric
  companyName: string
  /** Sources referenced by this metric's evidence_ids (resolved upstream). */
  evidenceTitles: string[]
  onInspect: (metric: Metric) => void
}

export function FinancialMetricCard({ metric, companyName, evidenceTitles, onInspect }: FinancialMetricCardProps) {
  const hasEvidence = metric.evidence_ids.length > 0
  return (
    <button
      type="button"
      onClick={() => onInspect(metric)}
      className="panel panel-hover w-full p-4 text-left"
      title="Inspect evidence"
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-fog-500">{companyName}</p>
        <ActualOrGuidanceBadge value={metric.actual_or_guidance} />
      </div>
      <p className="mt-2 text-sm font-semibold capitalize text-fog-200">{metric.metric_name.replaceAll('_', ' ')}</p>
      <p className="num mt-1 text-xl font-bold text-fog-100">
        {formatFullValue(metric.value)}
        <span className="ml-1 text-xs font-medium text-fog-500">
          {metric.currency && metric.currency !== metric.unit ? `${metric.currency} ` : ''}{metric.unit}
        </span>
      </p>
      <p className="num mt-1.5 text-[11px] text-fog-500">
        {!/\d{4}-\d{2}-\d{2}/.test(metric.fiscal_period) && <>{metric.fiscal_period} · </>}
        {metric.period_start ? `${metric.period_start} → ${metric.period_end}` : metric.period_end}
        {metric.capex_scope && ' · ' + capexScopeLabel(metric.capex_scope)}
        {metric.calculation_method === 'quarter_difference' && ' · Derived from cumulative cash flows'}
      </p>
      <div className="mt-2.5 flex items-center gap-1.5 border-t border-ink-700/50 pt-2 text-[10px] text-fog-600">
        {hasEvidence ? (
          <>
            <VerificationDot status="supported" className="!h-1.5 !w-1.5" />
            {metric.evidence_ids.length} evidence record{metric.evidence_ids.length === 1 ? '' : 's'}
          </>
        ) : (
          <>
            <VerificationDot status="insufficient" className="!h-1.5 !w-1.5" />
            No evidence records attached
          </>
        )}
      </div>
      {evidenceTitles.length > 0 && (
        <p className="mt-1.5 line-clamp-1 text-[10px] italic text-fog-600">{evidenceTitles.join(' · ')}</p>
      )}
    </button>
  )
}

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { BarChart3 } from 'lucide-react'
import type { MetricGroup } from '../../lib/metrics'
import { CHART_SERIES_COLORS, capexScopeLabel, companyById, groupLabel, observationKey } from '../../lib/metrics'
import { formatValue } from '../../lib/format'
import type { Company, Metric } from '../../types/api'
import { ActualOrGuidanceBadge } from '../ui/Badges'

const AOG_FILL: Record<string, string> = {
  actual: '#2dd4bf',
  guidance: '#6aa6f8',
  analyst_estimate: '#a78bfa',
  model_inference: '#566177',
}

interface FinancialChartProps {
  group: MetricGroup
  companies: Company[]
}

function TooltipContent({ active, payload, companies }: {
  active?: boolean
  payload?: Array<{ payload: Metric }>
  companies: Company[]
}) {
  if (!active || !payload || payload.length === 0) return null
  const metric = payload[0].payload as Metric
  const company = companyById(companies).get(metric.company_id)
  return (
    <div className="rounded-md border border-ink-600 bg-ink-850 px-3 py-2 text-xs shadow-xl">
      <p className="font-semibold text-fog-100">{company?.company_name ?? metric.company_id}</p>
      <p className="num mt-1 text-fog-300">
        {metric.value.toLocaleString('en-US')} {metric.currency ?? ''} {metric.unit}
      </p>
      <p className="num mt-0.5 text-[11px] text-fog-500">
        {metric.fiscal_period} · {metric.period_start ?? '?'} → {metric.period_end}
      </p>
      <p className="mt-0.5 text-[11px] text-fog-500">{metric.accounting_basis}</p>
    </div>
  )
}

/**
 * Comparison bar chart for one metric group. Rendered only when the group is
 * comparable (single unit, currency and period) — otherwise the caller shows
 * an explicit "not comparable" panel instead of forcing one axis.
 */
export function FinancialChart({ group, companies }: FinancialChartProps) {
  const names = companyById(companies)
  const data = group.metrics
  const aogKinds = [...new Set(group.metrics.map((m) => m.actual_or_guidance))]

  return (
    <div className="panel">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-ink-700/60 px-5 py-3.5">
        <div className="flex items-center gap-2">
          <BarChart3 size={15} className="text-signal-400" />
          <h3 className="text-sm font-semibold text-fog-100">{groupLabel(group)}</h3>
        </div>
        <p className="num text-[11px] text-fog-500">
          {group.currencies.filter(Boolean).join('/') || 'unitless'} {group.units.join('/')} ·{' '}
          {group.periods[0].split('|')[0]} → {group.periods[0].split('|')[1]}
          {group.comparisonKey && <span> · {group.fiscalPeriods[0]} · {group.bases[0]}</span>}
        </p>
      </header>

      <div className="p-5">
        {!!group.comparisonIssues?.length && (
          <p className="mb-2 text-xs text-fog-500">Other reporting scopes shown separately. {group.comparisonIssues.join(' ')}</p>
        )}
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 24, right: 8, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#212939" vertical={false} />
              <XAxis
                dataKey="company_id"
                tickFormatter={(id: string) => names.get(id)?.company_name ?? id}
                tick={{ fill: '#76839a', fontSize: 11 }}
                axisLine={{ stroke: '#212939' }}
                tickLine={false}
              />
              <YAxis
                tickFormatter={(v: number) => formatValue(v)}
                tick={{ fill: '#76839a', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                width={48}
              />
              <Tooltip content={<TooltipContent companies={companies} />} cursor={{ fill: '#141926' }} />
              <Bar dataKey="value" radius={[3, 3, 0, 0]} maxBarSize={64} isAnimationActive={false}>
                {data.map((m, i) => (
                  <Cell key={m.metric_id} fill={AOG_FILL[m.actual_or_guidance] ?? CHART_SERIES_COLORS[i % CHART_SERIES_COLORS.length]} />
                ))}
                <LabelList
                  dataKey="value"
                  position="top"
                  formatter={(label) => formatValue(Number(label))}
                  style={{ fill: '#aab4c4', fontSize: 10 }}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-ink-700/50 pt-3">
          {aogKinds.map((kind) => (
            <span key={kind} className="flex items-center gap-1.5 text-[10px] text-fog-500">
              <span
                className="inline-block h-2.5 w-2.5 rounded-sm"
                style={{ background: AOG_FILL[kind] ?? '#566177' }}
              />
              <ActualOrGuidanceBadge value={kind} />
            </span>
          ))}
          <span className="ml-auto text-[10px] text-fog-600">
            Fiscal periods shown as reported — fiscal and calendar years are not interchanged.
          </span>
        </div>
      </div>
    </div>
  )
}

/** Shown in place of a chart when the group cannot share an axis honestly. */
export function NotComparablePanel({ group, companies }: { group: MetricGroup; companies: Company[] }) {
  const names = companyById(companies)
  return (
    <div className="panel">
      <header className="flex items-center gap-2 border-b border-ink-700/60 px-5 py-3.5">
        <BarChart3 size={15} className="text-fog-500" />
        <h3 className="text-sm font-semibold text-fog-100">{groupLabel(group)}</h3>
        <span className="ml-auto rounded border border-amber-450/40 bg-amber-450/10 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-amber-450">
          NOT PLOTTED — INCOMPARABLE
        </span>
      </header>
      <div className="p-5">
        {group.comparisonIssues ? (
          <ul className="list-disc space-y-1 pl-4 text-xs text-fog-400">
            {group.comparisonIssues.map((issue) => <li key={issue}>{issue}</li>)}
          </ul>
        ) : (
          <p className="text-xs leading-relaxed text-fog-400">
            These records differ in unit, currency, reporting period, accounting basis or value type.
          </p>
        )}
        <ul className="mt-3 space-y-2">
          {group.metrics.map((m) => (
            <li
              key={group.comparisonIssues ? observationKey(m) : m.metric_id}
              className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 rounded-md border border-ink-700/70 bg-ink-900 px-3 py-2 text-xs"
            >
              <span className="font-medium text-fog-300">{names.get(m.company_id)?.company_name ?? m.company_id}</span>
              <span className="num text-fog-100">
                {m.value.toLocaleString('en-US')} {m.currency ?? ''} {m.unit}
              </span>
              <span className="num text-[11px] text-fog-500">
                {m.fiscal_period} · {m.period_start ?? '?'} → {m.period_end}
                {m.capex_scope && ' · ' + capexScopeLabel(m.capex_scope)}
              </span>
              <span className="text-[11px] text-fog-500">{m.accounting_basis}</span>
              <ActualOrGuidanceBadge value={m.actual_or_guidance} />
            </li>
          ))}
        </ul>
        <p className="mt-3 text-[10px] text-fog-600">
          Differences: units [{group.units.join(', ')}] · currencies [
          {group.currencies.map((c) => c ?? 'none').join(', ')}] · periods [
          {group.periods.map((p) => p.replace('|', '→')).join(', ')}]
        </p>
      </div>
    </div>
  )
}

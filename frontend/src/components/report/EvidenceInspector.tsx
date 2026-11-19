import { useEffect } from 'react'
import { ExternalLink, X } from 'lucide-react'
import type { Claim, Company, Source } from '../../types/api'
import { VerificationBadge } from '../ui/Badges'
import { formatDate, formatDateTime } from '../../lib/format'

/** Unified inspection target: a claim (with verifier status) or a metric (no status asserted). */
export interface EvidenceTarget {
  id: string
  kind: 'claim' | 'metric'
  title: string
  verificationStatus?: 'supported' | 'contradicted' | 'insufficient'
  fields: Array<{ label: string; value: string; mono?: boolean }>
  limitations: string[]
  evidenceIds: string[]
}

export function claimToTarget(claim: Claim, companies: Company[]): EvidenceTarget {
  const companyName = companies.find((c) => c.company_id === claim.company_id)?.company_name
  const fields: EvidenceTarget['fields'] = []
  if (companyName) fields.push({ label: 'Company', value: companyName })
  if (claim.claim_type) fields.push({ label: 'Claim type', value: claim.claim_type, mono: true })
  if (claim.metric_name) fields.push({ label: 'Metric', value: claim.metric_name, mono: true })
  if (claim.verification_status === 'supported' && claim.numeric_value !== null) {
    fields.push({
      label: 'Value',
      value: `${claim.numeric_value.toLocaleString('en-US')}${claim.unit ? ` ${claim.unit}` : ''}`,
    })
  }
  if (claim.period_start) fields.push({ label: 'Period start', value: claim.period_start })
  if (claim.period_end) fields.push({ label: 'Period end', value: claim.period_end })
  if (claim.accounting_basis) fields.push({ label: 'Basis', value: claim.accounting_basis })
  if (claim.actual_or_guidance) fields.push({ label: 'Kind', value: claim.actual_or_guidance })
  return {
    id: claim.claim_id,
    kind: 'claim',
    title: claim.text,
    verificationStatus: claim.verification_status,
    fields,
    limitations: claim.limitations,
    evidenceIds: claim.evidence_ids,
  }
}

export function metricToTarget(
  metric: { metric_id: string; metric_name: string; value: number; currency: string | null; unit: string; period_start: string | null; period_end: string; fiscal_period: string; accounting_basis: string; actual_or_guidance: string; evidence_ids: string[] },
  companyName: string,
): EvidenceTarget {
  return {
    id: metric.metric_id,
    kind: 'metric',
    title: `${companyName} — ${metric.metric_name}`,
    fields: [
      { label: 'Company', value: companyName },
      { label: 'Value', value: `${metric.value.toLocaleString('en-US')} ${metric.currency ?? ''} ${metric.unit}` },
      { label: 'Fiscal period', value: metric.fiscal_period },
      { label: 'Period', value: `${metric.period_start ?? '?'} → ${metric.period_end}` },
      { label: 'Basis', value: metric.accounting_basis },
      { label: 'Kind', value: metric.actual_or_guidance },
    ],
    limitations: [],
    evidenceIds: metric.evidence_ids,
  }
}

interface EvidenceInspectorProps {
  target: EvidenceTarget | null
  sources: Source[]
  onClose: () => void
}

/**
 * Side panel exposing the full evidence trail behind a claim or metric.
 * Status colors and text come from the backend only — a source URL never
 * upgrades anything to "supported".
 */
export function EvidenceInspector({ target, sources, onClose }: EvidenceInspectorProps) {
  useEffect(() => {
    if (!target) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [target, onClose])

  if (!target) return null

  const linkedSources = sources.filter((s) => target.evidenceIds.includes(s.source_id))
  const missingIds = target.evidenceIds.filter((id) => !sources.some((s) => s.source_id === id))

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label="Evidence inspector">
      <button
        type="button"
        aria-label="Close evidence inspector"
        onClick={onClose}
        className="absolute inset-0 bg-ink-950/70 backdrop-blur-[2px]"
      />
      <aside className="animate-slide-in-right absolute inset-y-0 right-0 flex w-full max-w-lg flex-col border-l border-ink-600 bg-ink-900 shadow-2xl">
        <header className="flex items-center justify-between border-b border-ink-700 px-5 py-4">
          <div>
            <p className="label-caps">
              Evidence inspector — {target.kind === 'claim' ? 'Claim' : 'Metric'}
            </p>
            <p className="num mt-0.5 font-mono text-[11px] text-fog-600">{target.id}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1.5 text-fog-500 transition-colors hover:bg-ink-800 hover:text-fog-100"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5">
          <section>
            {target.verificationStatus ? (
              <VerificationBadge status={target.verificationStatus} />
            ) : (
              <span className="rounded border border-ink-600 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-fog-400">
                Metric record — no verifier status applies
              </span>
            )}
            <p className="mt-3 rounded-md border border-ink-700 bg-ink-850 px-3.5 py-3 text-sm leading-relaxed text-fog-100">
              {target.title}
            </p>
          </section>

          {target.fields.length > 0 && (
            <section>
              <p className="label-caps">Structured fields</p>
              <dl className="num mt-2 grid grid-cols-[110px_minmax(0,1fr)] items-baseline gap-x-4 gap-y-1.5 rounded-md border border-ink-700/70 bg-ink-900 px-3.5 py-3 text-xs">
                {target.fields.map((f) => (
                  <div key={f.label} className="col-span-2 grid grid-cols-subgrid">
                    <dt className="text-fog-600">{f.label}</dt>
                    <dd className={`truncate text-right text-fog-300 ${f.mono ? 'font-mono' : ''}`} title={f.value}>
                      {f.value}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          )}

          <section>
            <p className="label-caps">Evidence records ({target.evidenceIds.length})</p>
            {target.evidenceIds.length === 0 ? (
              <div className="mt-2 rounded-md border border-amber-450/30 bg-amber-450/5 px-3.5 py-3 text-xs leading-relaxed text-amber-450">
                No evidence records are referenced.{' '}
                {target.kind === 'claim'
                  ? `Its status (${target.verificationStatus}) was assigned by the backend verifier without linked sources — treat it as unverified.`
                  : 'This figure cannot be traced to a source in this report.'}
              </div>
            ) : linkedSources.length === 0 && missingIds.length > 0 ? (
              <div className="mt-2 rounded-md border border-danger-450/30 bg-danger-450/5 px-3.5 py-3 text-xs leading-relaxed text-danger-450">
                None of the referenced evidence IDs ({target.evidenceIds.join(', ')}) exist in this
                report's source list. This is a backend data-integrity issue.
              </div>
            ) : (
              <ul className="mt-2 space-y-3">
                {linkedSources.map((s) => (
                  <li key={s.source_id} className="rounded-md border border-ink-700 bg-ink-850 p-3.5">
                    <p className="text-sm font-medium leading-snug text-fog-100">{s.title}</p>
                    <div className="num mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-fog-500">
                      <span className="font-medium text-fog-400">{s.publisher}</span>
                      {s.publication_date && <span>Published {formatDate(s.publication_date)}</span>}
                      {s.retrieved_at && <span>Retrieved {formatDateTime(s.retrieved_at)}</span>}
                      <span className="rounded border border-ink-600 px-1.5 py-px font-mono text-[10px]">
                        {s.source_type}
                      </span>
                    </div>
                    {s.quoted_text && (
                      <blockquote className="mt-2.5 border-l-2 border-signal-500/50 bg-ink-900 px-3 py-2 text-xs italic leading-relaxed text-fog-300">
                        “{s.quoted_text}”
                      </blockquote>
                    )}
                    {s.numeric_value !== null && (
                      <p className="num mt-2 text-xs text-fog-400">
                        Value in this source: {s.numeric_value.toLocaleString('en-US')} {s.unit}
                      </p>
                    )}
                    {s.tag_definition && <p className="mt-2 text-xs text-fog-400">{s.tag_definition}</p>}
                    {s.original_numeric_value && <p className="num mt-1 text-xs text-fog-500">
                      Original filed value: {s.original_numeric_value} {s.unit}
                    </p>}
                    {s.structured_locator && (
                      <p className="num mt-2 text-[10px] text-fog-600">
                        Locator: <span className="font-mono">{s.structured_locator}</span>
                      </p>
                    )}
                    <a
                      href={s.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-2.5 inline-flex items-center gap-1 text-[11px] font-medium text-info-400 hover:underline"
                    >
                      Open source <ExternalLink size={10} />
                    </a>
                    <p className="num mt-1 truncate text-[10px] text-fog-600" title={s.url}>
                      {s.url}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {target.limitations.length > 0 && (
            <section>
              <p className="label-caps">Limitations</p>
              <ul className="mt-2 space-y-1.5">
                {target.limitations.map((lim, i) => (
                  <li
                    key={i}
                    className="rounded-md border border-amber-450/25 bg-amber-450/5 px-3.5 py-2.5 text-xs leading-relaxed text-amber-450/90"
                  >
                    {lim}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </aside>
    </div>
  )
}

/** Display formatting helpers. All formatting is presentation-only; values stay untouched. */

export function formatValue(value: number): string {
  if (!Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return Number.isInteger(value) ? String(value) : value.toFixed(2)
}

export function formatFullValue(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return value.toLocaleString('en-US', { maximumFractionDigits: 4 })
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('en-US', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

export function formatDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: '2-digit' })
}

export function formatPeriodRange(periodStart: string | null, periodEnd: string): string {
  if (!periodStart) return periodEnd
  return `${periodStart} → ${periodEnd}`
}

export function titleCaseWords(raw: string): string {
  return raw
    .split(/[\s_]+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ')
}

/** Human label for known backend step codes; anything else is shown verbatim. */
const STEP_LABELS: Record<string, string> = {
  queued: 'Queued',
  resolving_companies: 'Resolving companies',
  checking_evidence: 'Verifying evidence',
  done: 'Done',
}

export function stepLabel(step: string): string {
  return STEP_LABELS[step] ?? titleCaseWords(step)
}

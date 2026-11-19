import type { DataMode, TaskStatus } from '../../types/api'

const STATUS_STYLE: Record<TaskStatus, { label: string; className: string }> = {
  pending: { label: 'Pending', className: 'bg-fog-500/10 text-fog-300 border-fog-500/30' },
  researching: { label: 'Researching', className: 'bg-info-400/10 text-info-400 border-info-400/30' },
  verifying: { label: 'Verifying', className: 'bg-info-400/10 text-info-400 border-info-400/30' },
  completed: { label: 'Completed', className: 'bg-signal-500/10 text-signal-400 border-signal-500/40' },
  partial: { label: 'Partial', className: 'bg-amber-450/10 text-amber-450 border-amber-450/40' },
  failed: { label: 'Failed', className: 'bg-danger-450/10 text-danger-450 border-danger-450/40' },
}

export function StatusBadge({ status, className = '' }: { status: TaskStatus; className?: string }) {
  const s = STATUS_STYLE[status]
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-[11px] font-semibold tracking-wide ${s.className} ${className}`}
    >
      {s.label}
    </span>
  )
}

const MODE_LABEL: Record<DataMode, string> = {
  live: 'LIVE',
  mixed: 'MIXED / INCOMPLETE',
  mock: 'MOCK DATA',
  unverified: 'PROVENANCE UNVERIFIED',
}

export function DataModeBadge({ mode, className = '' }: { mode: DataMode; className?: string }) {
  const style = mode === 'live'
    ? 'border-signal-500/40 bg-signal-500/10 text-signal-400'
    : 'border-amber-450/50 bg-amber-450/10 text-amber-450'
  return (
    <span className={`inline-flex rounded border px-2 py-0.5 text-[11px] font-semibold tracking-wide ${style} ${className}`}>
      {MODE_LABEL[mode]}
    </span>
  )
}

const RESOLUTION_STYLE: Record<string, { label: string; className: string }> = {
  resolved: { label: 'Resolved', className: 'text-signal-400 border-signal-500/40' },
  ambiguous: { label: 'Ambiguous', className: 'text-amber-450 border-amber-450/40' },
  unverified: { label: 'Unverified', className: 'text-fog-500 border-ink-600' },
}

export function ResolutionBadge({ status }: { status: string }) {
  const s = RESOLUTION_STYLE[status] ?? RESOLUTION_STYLE.unverified
  return (
    <span className={`rounded border px-1.5 py-px text-[10px] font-medium tracking-wide ${s.className}`}>
      {s.label}
    </span>
  )
}

const VERIFICATION_STYLE = {
  supported: { label: 'Verified', className: 'border-signal-500/50 bg-signal-500/10 text-signal-400' },
  contradicted: { label: 'Evidence conflict', className: 'border-danger-450/50 bg-danger-450/10 text-danger-450' },
  insufficient: { label: 'Insufficient evidence', className: 'border-amber-450/50 bg-amber-450/10 text-amber-450' },
} as const

export function VerificationBadge({ status, className = '' }: { status: 'supported' | 'contradicted' | 'insufficient'; className?: string }) {
  const s = VERIFICATION_STYLE[status]
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-[11px] font-semibold tracking-wide ${s.className} ${className}`}
    >
      <VerificationDot status={status} />
      {s.label}
    </span>
  )
}

export function VerificationDot({ status, className = '' }: { status: 'supported' | 'contradicted' | 'insufficient'; className?: string }) {
  const color =
    status === 'supported' ? 'bg-signal-400' : status === 'contradicted' ? 'bg-danger-450' : 'bg-amber-450'
  return <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${color} ${className}`} />
}

const AOG_LABEL: Record<string, { label: string; className: string }> = {
  actual: { label: 'Actual', className: 'text-signal-400 border-signal-500/40' },
  guidance: { label: 'Guidance', className: 'text-info-400 border-info-400/40' },
  analyst_estimate: { label: 'Analyst est.', className: 'text-fog-300 border-ink-600' },
  model_inference: { label: 'Model inference', className: 'text-fog-500 border-ink-600' },
}

export function ActualOrGuidanceBadge({ value }: { value: string }) {
  const s = AOG_LABEL[value] ?? { label: value, className: 'text-fog-500 border-ink-600' }
  return (
    <span className={`rounded border px-1.5 py-px text-[10px] font-medium tracking-wide ${s.className}`}>
      {s.label}
    </span>
  )
}

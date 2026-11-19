import { ChevronRight, Link2 } from 'lucide-react'
import type { Claim, Source } from '../../types/api'
import { VerificationBadge } from '../ui/Badges'

interface ClaimCardProps {
  claim: Claim
  sources: Source[]
  companyName: string | null
  onOpen: (claim: Claim) => void
}

export function ClaimCard({ claim, sources, companyName, onOpen }: ClaimCardProps) {
  const verifiedValue = claim.verification_status === 'supported' ? claim.numeric_value : null
  const linkedSources = sources.filter((s) => claim.evidence_ids.includes(s.source_id))
  const missingIds = claim.evidence_ids.filter((id) => !sources.some((s) => s.source_id === id))

  return (
    <button
      type="button"
      onClick={() => onOpen(claim)}
      className="panel panel-hover w-full p-4 text-left"
      aria-haspopup="dialog"
    >
      <div className="flex items-start justify-between gap-3">
        <VerificationBadge status={claim.verification_status} />
        <span className="flex shrink-0 items-center gap-1 text-[11px] text-fog-600">
          <Link2 size={11} />
          {claim.evidence_ids.length} evidence
          <ChevronRight size={12} />
        </span>
      </div>

      <p className="mt-2.5 text-sm leading-relaxed text-fog-100">{claim.text}</p>

      {(companyName || claim.metric_name || verifiedValue !== null) && (
        <div className="mt-2.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-fog-500">
          {companyName && <span>{companyName}</span>}
          {claim.metric_name && <span className="font-mono">{claim.metric_name}</span>}
          {verifiedValue !== null && (
            <span className="num">
              {verifiedValue.toLocaleString('en-US')}
              {claim.unit ? ` ${claim.unit}` : ''}
              {claim.period_end ? ` @ ${claim.period_end}` : ''}
            </span>
          )}
        </div>
      )}

      {linkedSources.length > 0 && (
        <p className="mt-2 line-clamp-1 text-[11px] italic text-fog-600">
          {linkedSources.map((s) => s.title).join(' · ')}
        </p>
      )}

      {claim.limitations.length > 0 && (
        <ul className="mt-2.5 space-y-1 border-t border-ink-700/50 pt-2">
          {claim.limitations.map((lim, i) => (
            <li key={i} className="text-[11px] leading-relaxed text-amber-450/90">
              ⚠ {lim}
            </li>
          ))}
        </ul>
      )}

      {missingIds.length > 0 && (
        <p className="mt-2 text-[11px] text-danger-450/90">
          ⚠ {missingIds.length} referenced evidence record{missingIds.length === 1 ? '' : 's'} not found
          in this report ({missingIds.join(', ')}) — data integrity issue.
        </p>
      )}
    </button>
  )
}

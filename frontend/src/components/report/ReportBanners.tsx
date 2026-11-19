import { AlertOctagon, FlaskConical } from 'lucide-react'

/**
 * Mode/status banners. A mock report must never read like real analysis,
 * and a partial report must say what is missing.
 */
export function MockBanner() {
  return (
    <div className="flex items-start gap-3 rounded-panel border border-amber-450/40 bg-amber-450/10 px-4 py-3">
      <FlaskConical size={16} className="mt-0.5 shrink-0 text-amber-450" />
      <div>
        <p className="text-sm font-semibold text-amber-450">Development Mock — No Verified Financial Data</p>
        <p className="mt-0.5 text-xs leading-relaxed text-amber-450/80">
          This report was produced by the development mock pipeline. No live sources were queried and
          no figures below are verified company disclosures. Do not treat any value in this report as
          real financial analysis.
        </p>
      </div>
    </div>
  )
}

export function PartialBanner({ reasons }: { reasons?: string[] }) {
  return (
    <div className="flex items-start gap-3 rounded-panel border border-amber-450/30 bg-amber-450/5 px-4 py-3">
      <AlertOctagon size={16} className="mt-0.5 shrink-0 text-amber-450" />
      <div>
        <p className="text-sm font-semibold text-amber-450">Partial report</p>
        {reasons?.length ? (
          <ul className="mt-1 list-disc pl-4 text-xs text-fog-300">
            {reasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        ) : (
          <p className="mt-0.5 text-xs leading-relaxed text-fog-400">
            The agent finished, but at least one material claim lacks sufficient evidence. Claims are
            marked individually — unsupported figures are never promoted to verified.
          </p>
        )}
      </div>
    </div>
  )
}

export function FailedBanner() {
  return (
    <div className="flex items-start gap-3 rounded-panel border border-danger-450/30 bg-danger-450/5 px-4 py-3">
      <AlertOctagon size={16} className="mt-0.5 shrink-0 text-danger-450" />
      <div>
        <p className="text-sm font-semibold text-danger-450">Failed research run</p>
        <p className="mt-0.5 text-xs leading-relaxed text-fog-400">
          This task ended in an error state. Any content below is what was salvageable and may be
          incomplete.
        </p>
      </div>
    </div>
  )
}

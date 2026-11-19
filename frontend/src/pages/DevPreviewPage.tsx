import { useState } from 'react'
import { FlaskConical } from 'lucide-react'
import { DEV_FIXTURE_EMPTY, DEV_FIXTURE_RICH } from '../dev/fixtures'
import { DataModeBadge, StatusBadge } from '../components/ui/Badges'
import { ReportView } from '../components/report/ReportView'

/**
 * DEV-ONLY route (/dev/preview): renders static fixtures through the real
 * report components to develop visual states without the backend.
 * Not registered in production builds; fixtures are never API data.
 */
export function DevPreviewPage() {
  const [which, setWhich] = useState<'rich' | 'empty'>('rich')
  const report = which === 'rich' ? DEV_FIXTURE_RICH : DEV_FIXTURE_EMPTY

  return (
    <div className="animate-fade-up space-y-5">
      <div className="flex items-center gap-3 rounded-panel border border-info-400/30 bg-info-400/5 px-4 py-3">
        <FlaskConical size={16} className="shrink-0 text-info-400" />
        <p className="text-xs leading-relaxed text-fog-300">
          <span className="font-semibold text-info-400">Development preview.</span> The content below
          is static fixture data for UI development. It is rendered through the real report
          components but is never fetched from or sent to the backend.
        </p>
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setWhich('rich')}
          className={which === 'rich' ? 'btn-primary py-1.5! text-xs' : 'btn-ghost py-1.5! text-xs'}
        >
          Rich fixture
        </button>
        <button
          type="button"
          onClick={() => setWhich('empty')}
          className={which === 'empty' ? 'btn-primary py-1.5! text-xs' : 'btn-ghost py-1.5! text-xs'}
        >
          Empty fixture
        </button>
        <span className="ml-2 flex items-center gap-1.5">
          <DataModeBadge mode={report.data_mode} />
          <StatusBadge status={report.status} />
        </span>
      </div>

      <ReportView report={report} />
    </div>
  )
}

import { Link } from 'react-router-dom'
import { ArrowRight, Clock, X } from 'lucide-react'
import type { RecentTaskEntry } from '../types/api'
import { StatusBadge } from './ui/Badges'
import { EmptyState } from './ui/EmptyState'
import { formatDateTime } from '../lib/format'
import { removeRecentTask } from '../lib/recent'

interface RecentTasksProps {
  entries: RecentTaskEntry[]
  onChanged: () => void
}

export function RecentTasks({ entries, onChanged }: RecentTasksProps) {
  if (entries.length === 0) {
    return (
      <EmptyState
        title="No research yet"
        detail="Started tasks appear here so you can return to a running job or a finished report."
      />
    )
  }

  return (
    <ul className="space-y-2">
      {entries.map((entry) => (
        <li key={entry.task_id} className="panel panel-hover relative">
          <Link
            to={entry.report_id ? `/report/${entry.report_id}` : `/task/${entry.task_id}`}
            className="block px-4 py-3"
          >
            <div className="flex items-center justify-between gap-3">
              <StatusBadge status={entry.status} />
              <span className="flex items-center gap-1 text-[11px] text-fog-600">
                <Clock size={11} />
                {formatDateTime(entry.created_at)}
              </span>
            </div>
            <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-fog-300">{entry.query}</p>
            {entry.companies.length > 0 && (
              <p className="mt-1.5 text-[11px] text-fog-600">{entry.companies.join(' · ')}</p>
            )}
            <span className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-signal-400">
              {entry.report_id ? 'Open report' : 'Open task'}
              <ArrowRight size={11} />
            </span>
          </Link>
          <button
            type="button"
            onClick={() => {
              removeRecentTask(entry.task_id)
              onChanged()
            }}
            aria-label="Remove from recent list"
            className="absolute right-2 top-2 rounded p-1 text-fog-600 opacity-60 transition-opacity hover:text-fog-300 focus:opacity-100"
          >
            <X size={12} />
          </button>
        </li>
      ))}
    </ul>
  )
}

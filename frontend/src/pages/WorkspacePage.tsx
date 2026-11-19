import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search } from 'lucide-react'
import { ResearchComposer } from '../components/ResearchComposer'
import { RecentTasks } from '../components/RecentTasks'
import { loadRecentTasks } from '../lib/recent'
import type { RecentTaskEntry } from '../types/api'

export function WorkspacePage() {
  const navigate = useNavigate()
  const [recent, setRecent] = useState<RecentTaskEntry[]>([])

  const refreshRecent = useCallback(() => {
    setRecent(loadRecentTasks())
  }, [])

  useEffect(() => {
    refreshRecent()
  }, [refreshRecent])

  return (
    <div className="animate-fade-up">
      <header className="mb-7">
        <div className="flex items-center gap-2 text-signal-400">
          <Search size={14} />
          <p className="label-caps text-signal-400!">Evidence-grounded financial research</p>
        </div>
        <h1 className="mt-2 text-2xl font-bold tracking-tight text-fog-100">Research workspace</h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-fog-500">
          Submit a natural-language question. The research agent resolves the companies, retrieves
          sources, validates the financial figures and reports back with claim-level evidence.
        </p>
      </header>

      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <ResearchComposer onSubmitted={(taskId) => void navigate(`/task/${taskId}`)} />

        <div className="space-y-3">
          <h2 className="label-caps px-1">Recent research</h2>
          <RecentTasks entries={recent} onChanged={refreshRecent} />
        </div>
      </div>
    </div>
  )
}

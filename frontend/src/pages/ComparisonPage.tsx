import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Scale, Users } from 'lucide-react'
import { api, ApiError } from '../api/client'
import type { Report } from '../types/api'
import { compareReports } from '../lib/metrics'
import { ensureRecentTask, loadRecentTasks, upsertRecentTask } from '../lib/recent'
import { DataModeBadge, StatusBadge } from '../components/ui/Badges'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorState } from '../components/ui/ErrorState'
import { FinancialChart, NotComparablePanel } from '../components/report/FinancialChart'

const MAX_REPORTS = 4

export function ComparisonPage() {
  const [recent, setRecent] = useState<{ taskId: string; reportId: string; query: string }[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [reports, setReports] = useState<Report[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [question, setQuestion] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const navigate = useNavigate()

  async function submitResearch(event: React.FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setSubmitError(null)
    try {
      const task = await api.createResearch({ query: question.trim(), companies: [], as_of_date: null, report_ids: selected })
      upsertRecentTask({
        task_id: task.task_id, report_id: null, query: question.trim(),
        companies: [], status: task.status, created_at: new Date().toISOString(),
      })
      void navigate(`/task/${task.task_id}`)
    } catch (error) {
      setSubmitError(error instanceof ApiError ? error.message : 'Research could not be submitted.')
    } finally {
      setSubmitting(false)
    }
  }

  useEffect(() => {
    let active = true
    const entries = loadRecentTasks()
    const display = () => setRecent(entries.flatMap((entry) => entry.report_id
      ? [{ taskId: entry.task_id, reportId: entry.report_id,
          query: entry.query === '(recovered task)' ? 'Research report' : entry.query }]
      : []))
    display()
    void Promise.allSettled(entries.map(async (entry) => {
      if (entry.query !== '(recovered task)' || !entry.report_id) return
      const report = await api.getReport(entry.report_id)
      if (!active) return
      entry.query = report.query
      ensureRecentTask(entry)
      display()
    }))
    return () => { active = false }
  }, [])

  const fetchSelected = useCallback(async (ids: string[]) => {
    setLoading(true)
    setLoadError(null)
    try {
      const fetched = await Promise.all(ids.map((id) => api.getReport(id)))
      setReports(fetched)
    } catch (err) {
      setReports([])
      setLoadError(
        err instanceof ApiError
          ? err.message
          : 'One of the selected reports could not be loaded from the backend.',
      )
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (selected.length === 0) {
      setReports([])
      setLoadError(null)
      return
    }
    void fetchSelected(selected)
  }, [selected, fetchSelected])

  function toggle(reportId: string) {
    setSelected((prev) =>
      prev.includes(reportId)
        ? prev.filter((id) => id !== reportId)
        : prev.length >= MAX_REPORTS
          ? prev
          : [...prev, reportId],
    )
  }

  const { companies, groups } = useMemo(() => compareReports(reports), [reports])

  return (
    <div className="animate-fade-up space-y-6">
      <header>
        <div className="flex items-center gap-2 text-signal-400">
          <Scale size={14} />
          <p className="label-caps text-signal-400!">Cross-report analysis</p>
        </div>
        <h1 className="mt-2 text-2xl font-bold tracking-tight text-fog-100">Company comparison</h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-fog-500">
          Select up to {MAX_REPORTS} finished reports to compare their metrics. Charts appear only
          where units, currencies, periods, accounting basis and actual/guidance status match — incomparable records are listed
          instead of forced onto one axis.
        </p>
      </header>

      <section className="panel p-5">
        <p className="label-caps">Finished reports</p>
        {recent.length === 0 ? (
          <div className="mt-3">
            <EmptyState
              title="No finished reports yet"
              detail="Reports appear here once research tasks have completed. Start a research run in the workspace first."
              icon={<Users size={18} />}
            >
              <Link to="/" className="btn-ghost text-xs">
                Go to workspace
              </Link>
            </EmptyState>
          </div>
        ) : (
          <ul className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
            {recent.map((r) => {
              const checked = selected.includes(r.reportId)
              const disabled = !checked && selected.length >= MAX_REPORTS
              return (
                <li key={r.reportId}>
                  <label
                    className={`flex cursor-pointer items-start gap-3 rounded-md border px-3.5 py-3 transition-colors duration-150 ${
                      checked
                        ? 'border-signal-500/50 bg-signal-500/10'
                        : disabled
                          ? 'cursor-not-allowed border-ink-700/50 opacity-40'
                          : 'border-ink-700 hover:border-ink-600'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={disabled}
                      onChange={() => toggle(r.reportId)}
                      className="mt-0.5 h-3.5 w-3.5 accent-teal-500"
                    />
                    <span className="min-w-0">
                      <span className="line-clamp-2 block text-xs leading-relaxed text-fog-200">
                        {r.query}
                      </span>
                      <span className="num mt-1 block truncate font-mono text-[10px] text-fog-600">
                        {r.reportId}
                      </span>
                    </span>
                  </label>
                </li>
              )
            })}
          </ul>
        )}
        <p className="mt-3 text-[11px] text-fog-600">
          {selected.length} / {MAX_REPORTS} selected
        </p>
      </section>

      {selected.length >= 2 && (
        <form className="panel space-y-3 p-4" onSubmit={submitResearch}>
          <label className="block" htmlFor="comparison-question">Comparative research question</label>
          <input className="w-full rounded border border-ink-700 bg-ink-900 p-2"
            id="comparison-question" value={question} onChange={(event) => setQuestion(event.target.value)}
            minLength={3} maxLength={2000} required />
          <button className="rounded border border-ink-700 px-3 py-2" type="submit"
            disabled={submitting || question.trim().length < 3 || loading || !!loadError}>
            {submitting ? 'Submitting...' : 'Start comparative research'}
          </button>
          {submitError && <p role="alert">{submitError}</p>}
        </form>
      )}
      {loading && <p className="text-xs text-fog-500">Loading selected reports…</p>}

      {loadError && <ErrorState title="Comparison data unavailable" message={loadError} />}

      {!loading && !loadError && selected.length > 0 && reports.length > 0 && (
        <>
          <section className="panel p-5">
            <div className="flex flex-wrap items-center gap-2">
              {reports.map((r) => (
                <span key={r.report_id} className="flex items-center gap-1.5">
                  <DataModeBadge mode={r.data_mode} />
                  <StatusBadge status={r.status} />
                </span>
              ))}
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {companies.map((c) => (
                <span
                  key={c.company_id}
                  className="rounded-full border border-ink-700 bg-ink-900 px-3 py-1 text-xs text-fog-300"
                >
                  {c.company_name}
                  {c.ticker ? ` (${c.ticker})` : ''}
                </span>
              ))}
            </div>
          </section>

          {groups.length === 0 ? (
            <EmptyState
              title="No comparable metrics found"
              detail="The selected reports contain no structured financial metrics, so there is nothing to compare yet."
            />
          ) : (
            groups.map((group) =>
              group.comparable && group.metrics.length >= 2 ? (
                <FinancialChart key={group.comparisonKey ?? group.metric_name} group={group} companies={companies} />
              ) : (
                <NotComparablePanel key={group.comparisonKey ?? group.metric_name} group={group} companies={companies} />
              ),
            )
          )}
        </>
      )}

      {selected.length === 0 && recent.length > 0 && (
        <EmptyState
          title="Select reports to compare"
          detail={`Tick up to ${MAX_REPORTS} reports above. Metrics are compared within matching financial periods and accounting definitions.`}
        />
      )}
    </div>
  )
}

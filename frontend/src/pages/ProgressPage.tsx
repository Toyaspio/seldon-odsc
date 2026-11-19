import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowRight, FileText, RadioTower } from 'lucide-react'
import { api, ApiError } from '../api/client'
import { usePolling } from '../hooks/usePolling'
import { TERMINAL_TASK_STATUSES, type TaskInfo } from '../types/api'
import { StatusBadge } from '../components/ui/Badges'
import { ErrorState } from '../components/ui/ErrorState'
import { SkeletonPanel } from '../components/ui/Skeleton'
import { stepLabel, formatDateTime } from '../lib/format'
import { ensureRecentTask, updateRecentTaskStatus } from '../lib/recent'

const STALLED_AFTER_MS = 240_000

const STATUS_DESCRIPTION: Record<string, string> = {
  pending: 'The task is queued in the backend.',
  researching: 'The agent is resolving companies and gathering data.',
  verifying: 'Claims are being checked against retrieved evidence.',
  completed: 'Research finished with verified results.',
  partial: 'Research finished, but some claims lack sufficient evidence.',
  failed: 'The task did not finish.',
}

function ProgressView({ task }: { task: TaskInfo }) {
  const pct = Math.max(0, Math.min(100, task.progress))
  return (
    <div className="panel p-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <StatusBadge status={task.status} />
          <span className="text-sm font-medium text-fog-100">{stepLabel(task.current_step)}</span>
        </div>
        <span className="num text-sm font-semibold text-fog-300">{pct}%</span>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-ink-800">
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            task.status === 'failed' ? 'bg-danger-450' : 'bg-signal-500'
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="mt-3 text-xs leading-relaxed text-fog-500">
        {STATUS_DESCRIPTION[task.status] ?? 'Waiting for backend status.'}
      </p>
      <dl className="num mt-4 grid grid-cols-2 gap-x-6 gap-y-2 border-t border-ink-700/60 pt-3 text-[11px]">
        <div className="flex justify-between gap-2">
          <dt className="text-fog-600">Task ID</dt>
          <dd className="num truncate font-mono text-fog-400" title={task.task_id}>
            {task.task_id}
          </dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-fog-600">Current step</dt>
          <dd className="font-mono text-fog-400">{task.current_step}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-fog-600">Created</dt>
          <dd className="text-fog-400">{formatDateTime(task.created_at)}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-fog-600">Updated</dt>
          <dd className="text-fog-400">{formatDateTime(task.updated_at)}</dd>
        </div>
      </dl>
    </div>
  )
}

export function ProgressPage() {
  const { taskId = '' } = useParams()
  const navigate = useNavigate()
  const [initialLoading, setInitialLoading] = useState(true)
  const [task, setTask] = useState<TaskInfo | null>(null)
  const [initialError, setInitialError] = useState<ApiError | null>(null)
  const [pollError, setPollError] = useState<ApiError | null>(null)
  const [stalled, setStalled] = useState(false)
  const navigatedRef = useRef(false)

  const terminal = task !== null && TERMINAL_TASK_STATUSES.includes(task.status)

  // Initial fetch: a 404 or terminal state here means polling never needs to start.
  useEffect(() => {
    let cancelled = false
    setInitialLoading(true)
    setInitialError(null)
    api
      .getTask(taskId)
      .then((t) => {
        if (cancelled) return
        setTask(t)
        ensureRecentTask({
          task_id: t.task_id,
          report_id: t.report_id,
          query: '(recovered task)',
          companies: [],
          status: t.status,
          created_at: t.created_at,
        })
      })
      .catch((err: unknown) => {
        if (!cancelled) setInitialError(err instanceof ApiError ? err : new ApiError('network', String(err)))
      })
      .finally(() => {
        if (!cancelled) setInitialLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [taskId])

  const fetcher = useCallback(() => api.getTask(taskId), [taskId])
  const isDone = useCallback((t: TaskInfo) => TERMINAL_TASK_STATUSES.includes(t.status), [])

  const onSample = useCallback((t: TaskInfo) => {
    setTask(t)
    setPollError(null)
    updateRecentTaskStatus(t.task_id, t.status, t.report_id)
  }, [])

  const onFinished = useCallback((_t: TaskInfo | null, error: ApiError | null) => {
    if (error) setPollError(error)
  }, [])

  const shouldPoll = !initialLoading && !initialError && task !== null && !terminal
  const { tickCount } = usePolling({
    fetcher,
    isDone,
    onSample,
    onFinished,
    enabled: shouldPoll,
  })

  // Stalled hint — the backend may legitimately take a while, so keep polling.
  useEffect(() => {
    if (!shouldPoll) return
    const timer = setTimeout(() => setStalled(true), STALLED_AFTER_MS)
    return () => clearTimeout(timer)
  }, [shouldPoll])

  // Auto-navigate to the report once a terminal state exposes one.
  useEffect(() => {
    if (!task || navigatedRef.current) return
    if (TERMINAL_TASK_STATUSES.includes(task.status) && task.report_id) {
      navigatedRef.current = true
      void navigate(`/report/${task.report_id}`)
    }
  }, [task, navigate])

  useEffect(() => () => setStalled(false), [])

  if (initialLoading) {
    return (
      <div className="animate-fade-up space-y-4">
        <SkeletonPanel />
        <SkeletonPanel lines={2} />
      </div>
    )
  }

  if (initialError) {
    return (
      <div className="animate-fade-up">
        <ErrorState
          title={initialError.status === 404 ? 'Task not found' : 'Could not load task'}
          message={
            initialError.status === 404
              ? `No task with ID "${taskId}" exists on the backend. It may have been created in an earlier session with a cleared database.`
              : initialError.message
          }
        />
        <div className="mt-4">
          <Link to="/" className="btn-ghost text-xs">
            Back to workspace
          </Link>
        </div>
      </div>
    )
  }

  if (!task) return null

  return (
    <div className="animate-fade-up mx-auto max-w-2xl space-y-5">
      <header>
        <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight text-fog-100">
          <RadioTower size={18} className="text-signal-400" />
          Research progress
        </h1>
        <p className="mt-1 text-xs text-fog-600">
          Task <span className="num font-mono">{task.task_id}</span> — this page recovers by task ID
          after a refresh.
        </p>
      </header>

      <ProgressView task={task} />

      {stalled && !terminal && (
        <p className="rounded-md border border-amber-450/30 bg-amber-450/5 px-3.5 py-2.5 text-xs leading-relaxed text-amber-450">
          This task has been running for over four minutes. Polling continues — the backend will mark
          interrupted tasks as failed on restart.
        </p>
      )}

      {pollError && !terminal && (
        <ErrorState title="Status polling interrupted" message={pollError.message} />
      )}

      {task.status === 'failed' && (
        <ErrorState
          title={task.error_code ? `Task failed — ${task.error_code}` : 'Task failed'}
          message={task.error_message ?? 'The backend did not provide an error description.'}
        />
      )}

      {terminal && task.report_id && (
        <div className="flex items-center justify-between rounded-panel border border-signal-500/30 bg-signal-900/40 px-5 py-4">
          <div className="flex items-center gap-2.5">
            <FileText size={16} className="text-signal-400" />
            <p className="text-sm font-medium text-fog-100">Report is ready</p>
          </div>
          <Link to={`/report/${task.report_id}`} className="btn-primary py-1.5! text-xs">
            Open report <ArrowRight size={13} />
          </Link>
        </div>
      )}

      {terminal && !task.report_id && task.status !== 'failed' && (
        <p className="rounded-md border border-amber-450/30 bg-amber-450/5 px-3.5 py-2.5 text-xs leading-relaxed text-amber-450">
          The task finished in state “{task.status}” but no report was attached. Ask the backend owner
          to check the task pipeline.
        </p>
      )}

      {shouldPoll && (
        <p className="text-center text-[11px] text-fog-600">
          Polling status… ({tickCount} update{tickCount === 1 ? '' : 's'})
        </p>
      )}

      <div>
        <Link to="/" className="text-xs font-medium text-fog-500 transition-colors hover:text-fog-300">
          ← Back to workspace
        </Link>
      </div>
    </div>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { Report, ResearchUsage } from '../types/api'
import { ErrorState } from '../components/ui/ErrorState'
import { SkeletonCards, SkeletonPanel } from '../components/ui/Skeleton'
import { ReportView } from '../components/report/ReportView'

export function ReportPage() {
  const { reportId = '' } = useParams()
  const [report, setReport] = useState<Report | null>(null)
  const [usage, setUsage] = useState<ResearchUsage>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    api
      .getReport(reportId)
      .then(setReport)
      .catch((err: unknown) =>
        setError(err instanceof ApiError ? err : new ApiError('network', String(err))),
      )
      .finally(() => setLoading(false))
  }, [reportId])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    setUsage(undefined)
    if (!report) return
    let active = true
    void api.getTask(report.task_id).then(task => {
      if (active) setUsage(task.resource_usage)
    }).catch(() => {})
    return () => { active = false }
  }, [report])

  if (loading) {
    return (
      <div className="animate-fade-up space-y-5">
        <SkeletonPanel lines={2} />
        <SkeletonCards />
        <SkeletonPanel />
      </div>
    )
  }

  if (error || !report) {
    return (
      <div className="animate-fade-up">
        <ErrorState
          title={error?.status === 404 ? 'Report not found' : 'Could not load report'}
          message={
            error?.status === 404
              ? `No report with ID "${reportId}" exists on the backend.`
              : (error?.message ?? 'Unknown error.')
          }
          onRetry={error?.status === 404 ? undefined : load}
        />
        <div className="mt-4">
          <Link to="/" className="btn-ghost text-xs">
            Back to workspace
          </Link>
        </div>
      </div>
    )
  }

  return <ReportView report={report} usage={usage} />
}

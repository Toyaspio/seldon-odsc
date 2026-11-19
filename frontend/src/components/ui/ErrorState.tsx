import type { ReactNode } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

interface ErrorStateProps {
  title: string
  message: string
  onRetry?: () => void
  children?: ReactNode
}

export function ErrorState({ title, message, onRetry, children }: ErrorStateProps) {
  return (
    <div
      role="alert"
      className="flex flex-col items-start rounded-panel border border-danger-450/30 bg-danger-450/5 px-5 py-4"
    >
      <div className="flex items-center gap-2">
        <AlertTriangle size={16} className="shrink-0 text-danger-450" />
        <p className="text-sm font-semibold text-fog-100">{title}</p>
      </div>
      <p className="mt-1.5 pl-6 text-xs leading-relaxed text-fog-300">{message}</p>
      {(onRetry || children) && (
        <div className="mt-3 flex items-center gap-2 pl-6">
          {onRetry && (
            <button type="button" onClick={onRetry} className="btn-ghost py-1.5! text-xs">
              <RefreshCw size={13} /> Retry
            </button>
          )}
          {children}
        </div>
      )}
    </div>
  )
}

import type { ReactNode } from 'react'
import { Inbox } from 'lucide-react'

interface EmptyStateProps {
  title: string
  detail?: string
  icon?: ReactNode
  children?: ReactNode
}

export function EmptyState({ title, detail, icon, children }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center rounded-panel border border-dashed border-ink-700 bg-ink-900/50 px-6 py-12 text-center">
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-lg border border-ink-700 bg-ink-800 text-fog-500">
        {icon ?? <Inbox size={18} />}
      </div>
      <p className="text-sm font-medium text-fog-300">{title}</p>
      {detail && <p className="mt-1 max-w-md text-xs leading-relaxed text-fog-500">{detail}</p>}
      {children && <div className="mt-4">{children}</div>}
    </div>
  )
}

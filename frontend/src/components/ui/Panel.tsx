import type { ReactNode } from 'react'

interface PanelProps {
  title?: string
  subtitle?: string
  actions?: ReactNode
  children: ReactNode
  className?: string
}

export function Panel({ title, subtitle, actions, children, className = '' }: PanelProps) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-4 border-b border-ink-700/60 px-5 py-3.5">
          <div>
            {title && <h2 className="text-sm font-semibold text-fog-100">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-fog-500">{subtitle}</p>}
          </div>
          {actions && <div className="shrink-0">{actions}</div>}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  )
}

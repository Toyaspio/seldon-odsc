import type { ReactNode } from 'react'
import { Sidebar } from './Sidebar'
import { NavLink } from 'react-router-dom'

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-ink-950 md:flex-row">
      <Sidebar />
      <nav aria-label="Mobile navigation" className="flex gap-4 border-b border-ink-700 px-4 py-3 text-sm text-fog-300 md:hidden"><NavLink to="/">Research</NavLink><NavLink to="/compare">Comparison</NavLink><NavLink to="/settings">Settings</NavLink></nav>
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-8 sm:py-8">{children}</div>
      </main>
    </div>
  )
}

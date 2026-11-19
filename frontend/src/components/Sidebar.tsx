import { NavLink } from 'react-router-dom'
import { FlaskConical, Scale, ShieldCheck, TerminalSquare, Settings } from 'lucide-react'
import { useHealth } from '../hooks/useHealth'

const NAV_ITEMS = [
  { to: '/', label: 'Research', icon: FlaskConical, end: true },
  { to: '/compare', label: 'Comparison', icon: Scale, end: false },
  { to: '/settings', label: 'Settings', icon: Settings, end: false },
]

function HealthIndicator() {
  const health = useHealth()
  if (health.kind === 'checking') {
    return <p className="text-[11px] text-fog-600">Checking backend…</p>
  }
  if (health.kind === 'down') {
    return (
      <div className="flex items-start gap-1.5">
        <span className="mt-1 inline-block h-2 w-2 shrink-0 rounded-full bg-danger-450" />
        <div>
          <p className="text-[11px] font-medium text-danger-450">Backend offline</p>
          <p className="mt-0.5 text-[10px] leading-snug text-fog-600">Start the FastAPI service to run research.</p>
        </div>
      </div>
    )
  }
  const mode = health.info.mode.toLowerCase()
  const isMock = mode === 'mock'
  const isLiveConfigured = mode === 'live-unverified' || mode === 'live'
  const label = isMock ? 'Mock mode' : isLiveConfigured ? 'Live configured' : 'Mode unrecognized'
  const detail = isMock
    ? 'Uses development fixtures.'
    : isLiveConfigured
      ? 'Provider authentication and research are unverified.'
      : `Backend reported mode: ${health.info.mode}.`
  return (
    <div className="flex items-start gap-1.5">
      <span
        className={`mt-1 inline-block h-2 w-2 shrink-0 rounded-full ${isLiveConfigured ? 'bg-signal-400' : 'bg-amber-450'}`}
      />
      <div>
        <p className="text-[11px] font-medium text-fog-300">
          Backend: {label}
        </p>
        <p className="mt-0.5 text-[10px] leading-snug text-fog-600">
          {detail}
        </p>
      </div>
    </div>
  )
}

export function Sidebar() {
  return (
    <aside className="hidden md:flex h-screen w-60 shrink-0 flex-col border-r border-ink-700/70 bg-ink-900">
      <div className="flex items-center gap-2.5 px-5 pb-5 pt-6">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-signal-500/40 bg-signal-900 text-signal-400">
          <TerminalSquare size={18} />
        </div>
        <div>
          <p className="text-[13px] font-bold tracking-[0.18em] text-fog-100">SELDON</p>
          <p className="text-[10px] font-medium tracking-[0.28em] text-fog-500">SYSTEM</p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 px-3">
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-md px-3 py-2 text-[13px] font-medium transition-colors duration-150 ${
                isActive
                  ? 'bg-ink-750 text-fog-100'
                  : 'text-fog-500 hover:bg-ink-800 hover:text-fog-300'
              }`
            }
          >
            <Icon size={15} />
            {label}
          </NavLink>
        ))}

        <div className="mt-6 border-t border-ink-700/60 px-3 pt-4">
          <p className="label-caps">Data integrity</p>
          <ul className="mt-2.5 space-y-2 text-[11px] leading-snug text-fog-500">
            <li className="flex items-start gap-2">
              <ShieldCheck size={12} className="mt-0.5 shrink-0 text-signal-500" />
              Every claim is traced to retrievable sources.
            </li>
            <li className="flex items-start gap-2">
              <ShieldCheck size={12} className="mt-0.5 shrink-0 text-signal-500" />
              Mock data is labeled and never shown as real analysis.
            </li>
          </ul>
        </div>
      </nav>

      <footer className="border-t border-ink-700/70 px-5 py-4">
        <HealthIndicator />
      </footer>
    </aside>
  )
}

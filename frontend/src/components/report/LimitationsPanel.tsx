import { ShieldAlert } from 'lucide-react'

export function LimitationsPanel({ limitations }: { limitations: string[] }) {
  if (limitations.length === 0) return null
  return (
    <div className="panel border-amber-450/25">
      <header className="flex items-center gap-2 border-b border-amber-450/20 px-5 py-3.5">
        <ShieldAlert size={15} className="text-amber-450" />
        <h3 className="text-sm font-semibold text-fog-100">Limitations & data caveats</h3>
        <span className="num ml-auto text-[11px] text-fog-600">{limitations.length}</span>
      </header>
      <ul className="space-y-2 p-5">
        {limitations.map((lim, i) => (
          <li key={i} className="flex items-start gap-2 text-xs leading-relaxed text-fog-300">
            <span className="mt-1.5 inline-block h-1 w-1 shrink-0 rounded-full bg-amber-450" />
            {lim}
          </li>
        ))}
      </ul>
    </div>
  )
}

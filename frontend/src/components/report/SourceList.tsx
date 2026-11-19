import { useState } from 'react'
import { ChevronDown, ExternalLink, Quote } from 'lucide-react'
import type { Source } from '../../types/api'
import { formatDate } from '../../lib/format'

export function SourceList({ sources }: { sources: Source[] }) {
  return (
    <ul className="space-y-2.5">
      {sources.map((s) => (
        <SourceItem key={s.source_id} source={s} />
      ))}
    </ul>
  )
}

function SourceItem({ source }: { source: Source }) {
  const [open, setOpen] = useState(false)
  const hasQuote = Boolean(source.quoted_text)
  return (
    <li className="panel panel-hover px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded border border-ink-600 px-1.5 py-px font-mono text-[10px] text-fog-500">
              {source.source_type}
            </span>
            <span className="text-[11px] font-medium text-fog-400">{source.publisher}</span>
            {source.issuer_name && (
              <span className="text-[11px] text-fog-500">Issuer: {source.issuer_name}</span>
            )}
            {source.publication_date && (
              <span className="num text-[11px] text-fog-600">{formatDate(source.publication_date)}</span>
            )}
          </div>
          <p className="mt-1.5 text-sm leading-snug text-fog-100">{source.title}</p>
          <a
            href={source.url}
            target="_blank"
            rel="noopener noreferrer"
            className="num mt-1 inline-flex max-w-full items-center gap-1 truncate text-[11px] text-info-400 hover:underline"
          >
            <ExternalLink size={10} className="shrink-0" />
            <span className="truncate">{source.url}</span>
          </a>
        </div>
        {hasQuote && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="btn-ghost shrink-0 px-2.5! py-1.5! text-[11px]"
            aria-expanded={open}
          >
            <Quote size={11} />
            Quote
            <ChevronDown size={11} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
          </button>
        )}
      </div>
      {open && source.quoted_text && (
        <blockquote className="mt-3 border-l-2 border-signal-500/50 bg-ink-900 px-3 py-2.5 text-xs italic leading-relaxed text-fog-300">
          “{source.quoted_text}”
        </blockquote>
      )}
      {source.structured_locator && (
        <p className="num mt-2 text-[10px] text-fog-600">
          Locator: <span className="font-mono">{source.structured_locator}</span>
        </p>
      )}
    </li>
  )
}

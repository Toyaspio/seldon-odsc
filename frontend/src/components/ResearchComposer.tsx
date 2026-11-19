import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import { upsertRecentTask } from '../lib/recent'

const EXAMPLE_QUESTIONS = [
  "Is Oracle's AI infrastructure spending financially sustainable?",
  'How do Microsoft and Alphabet compare in capital expenditure and cash flow?',
  "What evidence supports the commercialization of NVIDIA's AI infrastructure ecosystem?",
]

const RECOMMENDED_COMPANIES = ['Microsoft', 'Alphabet', 'Amazon', 'Meta']

interface ResearchComposerProps {
  onSubmitted: (taskId: string) => void
}

export function ResearchComposer({ onSubmitted }: ResearchComposerProps) {
  const [query, setQuery] = useState('')
  const [companies, setCompanies] = useState<string[]>([])
  const [customInput, setCustomInput] = useState('')
  const [recommended, setRecommended] = useState<string[]>(RECOMMENDED_COMPANIES)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .getCompanies()
      .then((res) => {
        if (!cancelled && Array.isArray(res.companies) && res.companies.length > 0) {
          setRecommended(res.companies)
        }
      })
      .catch(() => {
        // Fall back to static recommendations; the banner error is surfaced on submit.
      })
    return () => {
      cancelled = true
    }
  }, [])

  const queryValid = query.trim().length >= 3 && query.length <= 2000

  function toggleCompany(name: string) {
    setCompanies((prev) =>
      prev.includes(name) ? prev.filter((c) => c !== name) : [...prev, name],
    )
  }

  function addCustomCompany() {
    const name = customInput.trim()
    if (!name) return
    if (!companies.includes(name)) setCompanies((prev) => [...prev, name])
    setCustomInput('')
  }

  async function submit() {
    if (!queryValid || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const res = await api.createResearch({
        query: query.trim(),
        companies,
        as_of_date: null,
      })
      upsertRecentTask({
        task_id: res.task_id,
        report_id: null,
        query: query.trim(),
        companies,
        status: res.status,
        created_at: new Date().toISOString(),
      })
      onSubmitted(res.task_id)
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail ? `${err.message} (${err.detail})` : err.message)
      } else {
        setError(err instanceof Error ? err.message : 'Unexpected error while creating the task.')
      }
      setSubmitting(false)
    }
  }

  return (
    <div className="panel animate-fade-up">
      <div className="border-b border-ink-700/60 px-5 py-3.5">
        <h2 className="text-sm font-semibold text-fog-100">New research question</h2>
        <p className="mt-0.5 text-xs text-fog-500">
          Describe the research question and companies to investigate.
        </p>
      </div>

      <div className="p-5">
        <textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') void submit()
          }}
          rows={3}
          maxLength={2000}
          placeholder="e.g. Is Oracle's AI infrastructure spending financially sustainable?"
          className="input-base resize-y text-[15px] leading-relaxed"
          aria-label="Research question"
        />
        <div className="mt-1.5 flex items-center justify-between text-[11px] text-fog-600">
          <span>Ctrl + Enter to start</span>
          <span className={query.length > 1900 ? 'text-amber-450' : ''}>{query.length} / 2000</span>
        </div>

        <div className="mt-4">
          <p className="label-caps">Example questions</p>
          <div className="mt-2 space-y-1.5">
            {EXAMPLE_QUESTIONS.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => setQuery(q)}
                className="block w-full rounded-md border border-ink-700/70 bg-ink-900 px-3 py-2 text-left text-xs leading-relaxed text-fog-300 transition-colors duration-150 hover:border-ink-600 hover:text-fog-100"
              >
                {q}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-5">
          <p className="label-caps">Recommended companies</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {recommended.map((name) => {
              const selected = companies.includes(name)
              return (
                <button
                  key={name}
                  type="button"
                  onClick={() => toggleCompany(name)}
                  aria-pressed={selected}
                  className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors duration-150 ${
                    selected
                      ? 'border-signal-500/60 bg-signal-500/15 text-signal-400'
                      : 'border-ink-700 text-fog-300 hover:border-ink-600 hover:text-fog-100'
                  }`}
                >
                  {name}
                </button>
              )
            })}
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-fog-600">
            These are suggestions only — any listed company (NVIDIA, Oracle, Alibaba…) can be entered
            below or mentioned in the question.
          </p>
        </div>

        <div className="mt-4">
          <p className="label-caps">Add company or ticker</p>
          <div className="mt-2 flex gap-2">
            <input
              value={customInput}
              onChange={(e) => setCustomInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  addCustomCompany()
                }
              }}
              placeholder="e.g. NVIDIA, 1703.T, Alibaba"
              className="input-base flex-1"
              aria-label="Custom company name or ticker"
            />
            <button type="button" onClick={addCustomCompany} className="btn-ghost shrink-0">
              Add
            </button>
          </div>
          {companies.length > 0 && (
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {companies.map((name) => (
                <span
                  key={name}
                  className="inline-flex items-center gap-1.5 rounded-full border border-signal-500/60 bg-signal-500/15 py-1 pl-3 pr-1.5 text-xs font-medium text-signal-400"
                >
                  {name}
                  <button
                    type="button"
                    onClick={() => toggleCompany(name)}
                    aria-label={`Remove ${name}`}
                    className="rounded-full px-1 text-signal-400/70 hover:text-fog-100"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>

        {error && (
          <div
            role="alert"
            className="mt-4 rounded-md border border-danger-450/30 bg-danger-450/5 px-3.5 py-2.5 text-xs leading-relaxed text-danger-450"
          >
            {error}
          </div>
        )}

        <div className="mt-5 flex items-center justify-between border-t border-ink-700/60 pt-4">
          <p className="text-[11px] text-fog-600">
            The agent runs on the backend; nothing here calls external services directly.
          </p>
          <button
            type="button"
            onClick={() => void submit()}
            disabled={!queryValid || submitting}
            className="btn-primary"
          >
            {submitting ? 'Starting…' : 'Start research'}
          </button>
        </div>
      </div>
    </div>
  )
}

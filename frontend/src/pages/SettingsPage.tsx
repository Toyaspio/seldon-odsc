import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api/client'
import type { ConnectionChecks, HealthInfo, SettingsField, SettingsStatus } from '../types/api'

const fields = [
  { name: 'nebius_api_key', label: 'Nebius API key', type: 'password', maxLength: 512 },
  { name: 'tavily_api_key', label: 'Tavily API key', type: 'password', maxLength: 512 },
  { name: 'sec_contact_email', label: 'SEC contact email', type: 'email', maxLength: 320 },
] as const

const sourceLabels = {
  environment: 'Process environment',
  dotenv: '.env',
  local_settings: 'Local settings file',
  runtime: 'Current process',
} as const

const checkLabels = {
  not_configured: 'Not configured',
  reachable: 'Direct TLS reachable',
  unavailable: 'Direct TLS failed',
} as const

export function SettingsPage() {
  const [health, setHealth] = useState<HealthInfo>()
  const [healthFailed, setHealthFailed] = useState(false)
  const [settings, setSettings] = useState<SettingsStatus>()
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<SettingsField | 'connections'>()
  const [message, setMessage] = useState<string>()
  const [error, setError] = useState<string>()
  const [checks, setChecks] = useState<ConnectionChecks>()
  const [editingSecContact, setEditingSecContact] = useState(false)

  useEffect(() => {
    let active = true
    void api.getHealth().then(result => {
      if (active) setHealth(result)
    }).catch(() => {
      if (active) setHealthFailed(true)
    })
    void api.getSettings().then(result => {
      if (active) setSettings(result)
    }).catch(() => {
      if (active) setError('Local configuration could not be loaded.')
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [])

  async function save(event: FormEvent<HTMLFormElement>, field: SettingsField) {
    event.preventDefault()
    if (busy) return
    const form = event.currentTarget
    const value = String(new FormData(form).get(field) ?? '').trim()
    if (!value) return
    setBusy(field)
    setError(undefined)
    setMessage(undefined)
    try {
      const result = await api.saveSetting(field, value)
      form.reset()
      if (field === 'sec_contact_email') setEditingSecContact(false)
      setSettings(result)
      setChecks(undefined)
      setMessage('Saved. New requests in this process use the updated setting.')
    } catch {
      setError('Could not save this setting. Check the input and the local backend.')
    } finally {
      setBusy(undefined)
    }
  }

  async function checkConnections() {
    if (busy) return
    setBusy('connections')
    setChecks(undefined)
    setError(undefined)
    setMessage(undefined)
    try {
      setChecks(await api.checkSettingsConnections())
    } catch {
      setError('The backend could not complete the connection check.')
    } finally {
      setBusy(undefined)
    }
  }

  return <div className="max-w-2xl space-y-4 text-fog-100">
    <h1 className="text-2xl font-bold">Settings</h1>
    <section className="panel space-y-3 p-5" aria-labelledby="runtime-heading">
      <h2 id="runtime-heading" className="font-semibold">Research runtime</h2>
      {healthFailed && <p className="text-amber-450">Backend status unavailable.</p>}
      {!healthFailed && !health && <p role="status">Loading backend status…</p>}
      {health && <dl className="grid gap-4 sm:grid-cols-2">
        <div><dt className="text-sm text-fog-500">Inference provider</dt><dd>{health.provider || 'Unavailable'}</dd></div>
        <div><dt className="text-sm text-fog-500">Model</dt><dd>{health.model || 'Fixture mode'}</dd></div>
      </dl>}
      {health?.mode === 'mock' && <p className="text-sm text-fog-400">
        This server is running in Mock mode. Start it with LIVE providers enabled to run real research.
      </p>}
    </section>

    {loading && <p role="status">Loading local configuration…</p>}
    {settings && <>
      {fields.map(({ name, label, type, maxLength }) => {
        const configured = settings.settings[name]
        const showInput = name !== 'sec_contact_email' || !configured || editingSecContact
        const source = settings.effective_sources[name]
        const startup = settings.startup_overrides[name]
        return <form key={name} method="post" autoComplete="off" aria-label={label}
          onSubmit={event => void save(event, name)} className="panel space-y-2 p-5">
          {showInput
            ? <label htmlFor={name} className="font-semibold">{label}</label>
            : <h2 className="font-semibold">{label}</h2>}
          <p className="text-sm text-fog-400">
            {configured ? 'Configured' : 'Not configured'}
            {source && ' · Effective source: ' + sourceLabels[source]}
          </p>
          {startup && settings.saved_keys[name] && <p className="text-sm text-amber-450">
            {source !== 'local_settings'
              ? 'The startup configuration takes priority over the saved value. '
              : 'The saved value is active in this process. '}
            On restart, {sourceLabels[startup]} takes priority if it still provides a value.
          </p>}
          {showInput ? <div className="flex flex-wrap gap-2">
            <input id={name} name={name} type={type} autoComplete={type === 'password' ? 'new-password' : 'off'}
              required maxLength={maxLength} disabled={!!busy} spellCheck={false}
              className="min-w-48 flex-1 rounded border border-ink-700 bg-ink-950 px-3 py-2 text-sm" />
            <button type="submit" className="btn-primary disabled:opacity-50" disabled={!!busy}>
              {busy === name ? 'Saving…' : 'Save'}
            </button>
            {name === 'sec_contact_email' && configured && <button type="button" className="btn-ghost"
              disabled={!!busy} onClick={() => setEditingSecContact(false)}>Cancel</button>}
          </div> : <button type="button" className="btn-ghost" disabled={!!busy}
            onClick={() => setEditingSecContact(true)}>Change email</button>}
        </form>
      })}

      <section aria-labelledby="connection-heading" className="panel space-y-3 p-5">
        <h2 id="connection-heading" className="font-semibold">Provider connections</h2>
        <p className="text-sm text-fog-400">This check tests direct TLS connectivity. Research requests may use the system proxy. API authentication and model inference are untested.</p>
        <button type="button" className="btn-ghost" disabled={!!busy} onClick={() => void checkConnections()}>
          {busy === 'connections' ? 'Checking…' : 'Check connections'}
        </button>
        {checks && <dl className="grid gap-4 text-sm sm:grid-cols-2" aria-live="polite">
          <div><dt>Nebius</dt><dd>{checkLabels[checks.nebius]}</dd></div>
          <div><dt>Tavily</dt><dd>{checkLabels[checks.tavily]}</dd></div>
        </dl>}
      </section>
    </>}
    {message && <p role="status" className="text-sm text-signal-400">{message}</p>}
    {error && <p role="alert" className="text-sm text-amber-450">{error}</p>}
  </div>
}

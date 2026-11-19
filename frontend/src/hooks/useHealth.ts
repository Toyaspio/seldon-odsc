import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { HealthInfo } from '../types/api'

export type HealthState =
  | { kind: 'checking' }
  | { kind: 'ok'; info: HealthInfo }
  | { kind: 'down'; message: string }

/** Poll backend health every 30s for the sidebar status indicator. */
export function useHealth(): HealthState {
  const [state, setState] = useState<HealthState>({ kind: 'checking' })

  useEffect(() => {
    let cancelled = false
    const check = async () => {
      try {
        const info = await api.getHealth()
        if (!cancelled) setState({ kind: 'ok', info })
      } catch (err) {
        if (!cancelled) {
          setState({
            kind: 'down',
            message: err instanceof Error ? err.message : 'Backend unreachable',
          })
        }
      }
    }
    check()
    const timer = setInterval(check, 30_000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  return state
}

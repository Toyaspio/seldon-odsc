import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'

interface PollingOptions<T> {
  /** Fetch one sample. Should throw ApiError on failure. */
  fetcher: () => Promise<T>
  /** Return true to stop polling (terminal state reached). */
  isDone: (sample: T) => boolean
  /** Called with each successful sample. */
  onSample?: (sample: T) => void
  /** Called when polling stops (done or terminal error). */
  onFinished?: (sample: T | null, error: ApiError | null) => void
  intervalMs?: number
  maxIntervalMs?: number
  /** Stop after this many consecutive failures. */
  maxConsecutiveErrors?: number
  /** When false, polling stays idle until it becomes true. */
  enabled?: boolean
}

type PollingState = 'idle' | 'polling' | 'done' | 'error'

/**
 * Interval polling with linear backoff and terminal-state detection.
 * Stops on terminal state, repeated failures, or unmount.
 */
export function usePolling<T>(options: PollingOptions<T>) {
  const {
    fetcher,
    isDone,
    onSample,
    onFinished,
    intervalMs = 1500,
    maxIntervalMs = 4000,
    maxConsecutiveErrors = 5,
    enabled = true,
  } = options

  const [state, setState] = useState<PollingState>('idle')
  const [lastError, setLastError] = useState<ApiError | null>(null)
  const [tickCount, setTickCount] = useState(0)

  // Keep latest callbacks without resetting the timer.
  const fetcherRef = useRef(fetcher)
  const isDoneRef = useRef(isDone)
  const onSampleRef = useRef(onSample)
  const onFinishedRef = useRef(onFinished)
  fetcherRef.current = fetcher
  isDoneRef.current = isDone
  onSampleRef.current = onSample
  onFinishedRef.current = onFinished

  useEffect(() => {
    if (!enabled) {
      setState('idle')
      return
    }
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let consecutiveErrors = 0
    let currentInterval = intervalMs
    let finished = false

    const finish = (sample: T | null, error: ApiError | null) => {
      if (finished || stopped) return
      finished = true
      setState(error ? 'error' : 'done')
      onFinishedRef.current?.(sample, error)
    }

    const run = async () => {
      if (stopped || finished) return
      try {
        const sample = await fetcherRef.current()
        if (stopped || finished) return
        consecutiveErrors = 0
        currentInterval = intervalMs
        setLastError(null)
        setState('polling')
        setTickCount((n) => n + 1)
        onSampleRef.current?.(sample)
        if (isDoneRef.current(sample)) {
          finish(sample, null)
          return
        }
      } catch (err) {
        if (stopped || finished) return
        consecutiveErrors += 1
        const apiError =
          err instanceof ApiError
            ? err
            : new ApiError('network', err instanceof Error ? err.message : 'Unknown polling error')
        setLastError(apiError)
        if (consecutiveErrors >= maxConsecutiveErrors) {
          finish(null, apiError)
          return
        }
        currentInterval = Math.min(currentInterval + 500, maxIntervalMs)
      }
      if (!stopped && !finished) {
        timer = setTimeout(run, currentInterval)
      }
    }

    setState('polling')
    run()

    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, fetcherKey(options)])

  return { state, lastError, tickCount }
}

/** Stable key so callers can pass inline closures without restarting polling. */
function fetcherKey(_options: unknown): string {
  return 'poll'
}

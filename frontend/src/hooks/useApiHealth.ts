import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchApiHealth } from '../services/healthService'
import type { ApiConnectionStatus } from '../types/api'

const DEFAULT_POLL_INTERVAL_MS = 30_000

export interface ApiHealthSnapshot {
  status: ApiConnectionStatus
  /** Measured round-trip time of the last successful check. */
  latencyMs: number | null
  lastCheckedAt: Date | null
}

export interface ApiHealth extends ApiHealthSnapshot {
  recheck: () => void
}

const INITIAL_SNAPSHOT: ApiHealthSnapshot = {
  status: 'checking',
  latencyMs: null,
  lastCheckedAt: null,
}

/** Polls the backend health endpoint and reports only what that endpoint actually returns. */
export function useApiHealth(pollIntervalMs = DEFAULT_POLL_INTERVAL_MS): ApiHealth {
  const [snapshot, setSnapshot] = useState<ApiHealthSnapshot>(INITIAL_SNAPSHOT)
  const runCheckRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    const runCheck = async () => {
      const startedAt = performance.now()
      try {
        const health = await fetchApiHealth(controller.signal)
        if (controller.signal.aborted) return
        setSnapshot({
          status: health.status === 'healthy' ? 'online' : 'degraded',
          latencyMs: Math.round(performance.now() - startedAt),
          lastCheckedAt: new Date(),
        })
      } catch {
        if (controller.signal.aborted) return
        setSnapshot({ status: 'offline', latencyMs: null, lastCheckedAt: new Date() })
      }
    }

    const runCheckIfVisible = () => {
      if (document.visibilityState === 'visible') void runCheck()
    }

    runCheckRef.current = () => void runCheck()
    void runCheck()
    // Skip polling in background tabs; re-check as soon as the tab is visible again.
    const intervalId = window.setInterval(runCheckIfVisible, pollIntervalMs)
    document.addEventListener('visibilitychange', runCheckIfVisible)

    return () => {
      runCheckRef.current = null
      controller.abort()
      window.clearInterval(intervalId)
      document.removeEventListener('visibilitychange', runCheckIfVisible)
    }
  }, [pollIntervalMs])

  const recheck = useCallback(() => {
    setSnapshot((current) => ({ ...current, status: 'checking' }))
    runCheckRef.current?.()
  }, [])

  return useMemo(() => ({ ...snapshot, recheck }), [snapshot, recheck])
}

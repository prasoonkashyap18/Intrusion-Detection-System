import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getHealth, isApiError } from '../services'
import type { ApiConnectionStatus } from '../types/api'

/**
 * Light periodic re-check: the header badge and Platform Status panel should
 * notice a backend that went down without the user reloading, but connectivity
 * changes rarely, so this stays well clear of aggressive polling.
 */
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
  status: 'connecting',
  latencyMs: null,
  lastCheckedAt: null,
}

/**
 * Owns the health-check lifecycle for the UI: reports `connecting` until the
 * first response, then only what the backend actually returns. Nothing here
 * invents a healthy state.
 */
export function useApiHealth(pollIntervalMs = DEFAULT_POLL_INTERVAL_MS): ApiHealth {
  const [snapshot, setSnapshot] = useState<ApiHealthSnapshot>(INITIAL_SNAPSHOT)
  const runCheckRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    const runCheck = async () => {
      const startedAt = performance.now()
      try {
        const health = await getHealth(controller.signal)
        if (controller.signal.aborted) return
        setSnapshot({
          status: health.status === 'healthy' ? 'online' : 'degraded',
          latencyMs: Math.round(performance.now() - startedAt),
          lastCheckedAt: new Date(),
        })
      } catch (error) {
        // A cancelled request says nothing about backend health.
        if (controller.signal.aborted || (isApiError(error) && error.code === 'aborted')) return
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
    setSnapshot((current) => ({ ...current, status: 'connecting' }))
    runCheckRef.current?.()
  }, [])

  return useMemo(() => ({ ...snapshot, recheck }), [snapshot, recheck])
}

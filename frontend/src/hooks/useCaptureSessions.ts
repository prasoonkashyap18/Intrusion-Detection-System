import { useCallback, useEffect, useMemo, useState } from 'react'
import { getCaptureSessions } from '../services/capture'
import type { CaptureSession } from '../types/capture'
import { describeLoadFailure, type LoadFailure } from '../utils/loadFailure'

export interface UseCaptureSessions {
  sessions: CaptureSession[] | null
  failure: LoadFailure | null
  isLoading: boolean
  refresh: () => void
}

/** Loads every known capture session (GET /api/v1/capture/sessions) —
 * used for the dashboard's monitoring-status panel. An empty/offline
 * capture backend on this host (see backend/README.md's "Live Traffic
 * Capture" section) is a real, reportable state, never hidden. */
export function useCaptureSessions(): UseCaptureSessions {
  const [token, setToken] = useState(0)
  const [settledToken, setSettledToken] = useState<number | null>(null)
  const [sessions, setSessions] = useState<CaptureSession[] | null>(null)
  const [failure, setFailure] = useState<LoadFailure | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    getCaptureSessions(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return
        setSessions(result.items)
        setFailure(null)
        setSettledToken(token)
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setFailure(describeLoadFailure(error))
        setSettledToken(token)
      })
    return () => controller.abort()
  }, [token])

  const refresh = useCallback(() => setToken((current) => current + 1), [])

  return useMemo(
    () => ({ sessions, failure, isLoading: settledToken !== token, refresh }),
    [sessions, failure, settledToken, token, refresh],
  )
}

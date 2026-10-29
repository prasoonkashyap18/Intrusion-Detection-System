import { useCallback, useEffect, useMemo, useState } from 'react'
import { getDashboardOverview } from '../services/dashboard'
import type { DashboardOverview } from '../types/dashboard'
import { describeLoadFailure, type LoadFailure } from '../utils/loadFailure'

export interface UseDashboardOverview {
  data: DashboardOverview | null
  failure: LoadFailure | null
  isLoading: boolean
  refresh: () => void
}

/** Loads real SOC overview metrics (GET /api/v1/dashboard/overview).
 * Mirrors `useDetectionBatches`'s own request-lifecycle pattern:
 * "loading" is derived from comparing the in-flight request token
 * against the last-settled one, rather than a separate `setState`
 * called synchronously inside the effect. */
export function useDashboardOverview(): UseDashboardOverview {
  const [token, setToken] = useState(0)
  const [settledToken, setSettledToken] = useState<number | null>(null)
  const [data, setData] = useState<DashboardOverview | null>(null)
  const [failure, setFailure] = useState<LoadFailure | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    getDashboardOverview(controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return
        setData(result)
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
    () => ({ data, failure, isLoading: settledToken !== token, refresh }),
    [data, failure, settledToken, token, refresh],
  )
}

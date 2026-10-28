import { useCallback, useEffect, useMemo, useState } from 'react'
import { getDetectionResults } from '../services'
import type { DetectionResultListResponse } from '../types/detection'
import { describeLoadFailure, type LoadFailure } from '../utils/loadFailure'

export const DETECTION_RESULTS_PAGE_SIZE = 10

export interface DetectionResults {
  /** Latest successfully loaded page for the current batch. Kept while a refresh or page change is in flight or has failed. */
  data: DetectionResultListResponse | null
  /** Most recent failure, cleared by the next success. With `data` present, this is a refresh failure. */
  failure: LoadFailure | null
  /** True while a request is in flight, including the first. */
  isLoading: boolean
  page: number
  nextPage: () => void
  previousPage: () => void
  /** Re-requests the current page. Ignored while a request is already in flight. */
  refresh: () => void
}

interface PageRequest {
  batchId: string
  page: number
  /** Changes identity of the request so the same page can be fetched again. */
  token: number
}

/**
 * Loads one batch's persisted detection results. Presentation-free, mirroring
 * useDetectionBatches: owns the request lifecycle, paging and retry, nothing
 * else. Never re-runs inference or feature extraction — it only reads
 * `DetectionResult` rows an earlier processing run already persisted.
 *
 * Switching `batchId` resets to page 1 rather than keeping whatever page a
 * previously viewed batch had settled on.
 */
export function useDetectionResults(batchId: string): DetectionResults {
  const [request, setRequest] = useState<PageRequest>({ batchId, page: 1, token: 0 })
  const [settled, setSettled] = useState<PageRequest | null>(null)
  const [data, setData] = useState<DetectionResultListResponse | null>(null)
  const [failure, setFailure] = useState<LoadFailure | null>(null)

  if (request.batchId !== batchId) {
    // Derived-state reset during render (the officially sanctioned pattern
    // for "a prop changed, start over") rather than an effect, so the stale
    // batch's page/data is never fetched or shown even for one render.
    setRequest({ batchId, page: 1, token: 0 })
  }

  useEffect(() => {
    const controller = new AbortController()

    getDetectionResults(request.batchId, request.page, DETECTION_RESULTS_PAGE_SIZE, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return
        setData(result)
        setFailure(null)
        setSettled(request)
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setFailure(describeLoadFailure(error))
        setSettled(request)
      })

    return () => controller.abort()
  }, [request])

  const isCurrent = settled !== null && settled.batchId === batchId
  const isLoading = !isCurrent || settled.token !== request.token || settled.page !== request.page

  const refresh = useCallback(() => {
    if (isLoading) return
    setRequest((current) => ({ ...current, token: current.token + 1 }))
  }, [isLoading])

  const totalPages = isCurrent ? data?.total_pages ?? 0 : 0
  const nextPage = useCallback(
    () =>
      setRequest((current) =>
        current.page >= totalPages ? current : { ...current, page: current.page + 1, token: current.token + 1 },
      ),
    [totalPages],
  )
  const previousPage = useCallback(
    () =>
      setRequest((current) =>
        current.page <= 1 ? current : { ...current, page: current.page - 1, token: current.token + 1 },
      ),
    [],
  )

  return useMemo(() => {
    // Not `isCurrent` means data/failure belong to a different batch than
    // the one currently requested — hide them rather than show stale results.
    if (!isCurrent) return { data: null, failure: null, isLoading, page: request.page, nextPage, previousPage, refresh }
    return { data, failure, isLoading, page: request.page, nextPage, previousPage, refresh }
  }, [isCurrent, data, failure, isLoading, request.page, nextPage, previousPage, refresh])
}

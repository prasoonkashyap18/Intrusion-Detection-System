import { useCallback, useEffect, useMemo, useState } from 'react'
import { getDetectionBatches } from '../services'
import type { DetectionBatchListResponse } from '../types/detection'
import { describeLoadFailure, type LoadFailure } from '../utils/loadFailure'

export const BATCHES_PAGE_SIZE = 10

export interface DetectionBatches {
  /** Latest successfully loaded page. Kept while a refresh or page change is in flight or has failed. */
  data: DetectionBatchListResponse | null
  /** Most recent failure, cleared by the next success. With `data` present, this is a refresh failure. */
  failure: LoadFailure | null
  /** True while a request is in flight, including the first. */
  isLoading: boolean
  page: number
  nextPage: () => void
  previousPage: () => void
  /** Re-requests the current page. Ignored while a request is already in flight. */
  refresh: () => void
  /** Jumps to page 1 and re-requests it, e.g. after a new upload. */
  reloadFirstPage: () => void
}

interface PageRequest {
  page: number
  /** Changes identity of the request so the same page can be fetched again. */
  token: number
}

/**
 * Loads persisted batches from the backend. Presentation-free: it owns the
 * request lifecycle, paging and retry, and leaves how that looks to the caller.
 *
 * "Loading" is derived rather than stored: a request is in flight until it has
 * settled, so no state has to be set synchronously from an effect.
 */
export function useDetectionBatches(): DetectionBatches {
  const [request, setRequest] = useState<PageRequest>({ page: 1, token: 0 })
  const [settled, setSettled] = useState<PageRequest | null>(null)
  const [data, setData] = useState<DetectionBatchListResponse | null>(null)
  const [failure, setFailure] = useState<LoadFailure | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    getDetectionBatches(request.page, BATCHES_PAGE_SIZE, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return
        if (result.items.length === 0 && result.total_pages > 0 && request.page > result.total_pages) {
          // The requested page no longer exists (e.g. batches were removed): show the last one.
          setRequest((current) => ({ page: result.total_pages, token: current.token + 1 }))
          return
        }
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

  const isLoading = settled !== request

  const refresh = useCallback(() => {
    if (isLoading) return
    setRequest((current) => ({ ...current, token: current.token + 1 }))
  }, [isLoading])

  const reloadFirstPage = useCallback(() => setRequest((current) => ({ page: 1, token: current.token + 1 })), [])

  const totalPages = data?.total_pages ?? 0
  const nextPage = useCallback(
    () =>
      setRequest((current) =>
        current.page >= totalPages ? current : { page: current.page + 1, token: current.token + 1 },
      ),
    [totalPages],
  )
  const previousPage = useCallback(
    () => setRequest((current) => (current.page <= 1 ? current : { page: current.page - 1, token: current.token + 1 })),
    [],
  )

  return useMemo(
    () => ({ data, failure, isLoading, page: request.page, nextPage, previousPage, refresh, reloadFirstPage }),
    [data, failure, isLoading, request.page, nextPage, previousPage, refresh, reloadFirstPage],
  )
}

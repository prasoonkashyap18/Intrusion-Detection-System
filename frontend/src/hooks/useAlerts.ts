import { useCallback, useEffect, useMemo, useState } from 'react'
import { getAlerts, type AlertListFilters } from '../services/alerts'
import type { AlertListResponse } from '../types/alert'
import { describeLoadFailure, type LoadFailure } from '../utils/loadFailure'

export const ALERTS_PAGE_SIZE = 10

export interface UseAlerts {
  data: AlertListResponse | null
  failure: LoadFailure | null
  isLoading: boolean
  page: number
  nextPage: () => void
  previousPage: () => void
  refresh: () => void
  setFilters: (filters: AlertListFilters) => void
  filters: AlertListFilters
}

interface PageRequest {
  page: number
  filters: AlertListFilters
  token: number
}

/** Loads persisted alerts from the backend (GET /api/v1/alerts),
 * mirroring `useDetectionBatches`'s own request-lifecycle/paging
 * pattern. Changing a filter resets to page 1, since a filtered result
 * set's own page count can differ from the unfiltered one. */
export function useAlerts(initialFilters: AlertListFilters = {}): UseAlerts {
  const [request, setRequest] = useState<PageRequest>({ page: 1, filters: initialFilters, token: 0 })
  const [settled, setSettled] = useState<PageRequest | null>(null)
  const [data, setData] = useState<AlertListResponse | null>(null)
  const [failure, setFailure] = useState<LoadFailure | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    getAlerts(request.page, ALERTS_PAGE_SIZE, request.filters, controller.signal)
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request.page, request.token, JSON.stringify(request.filters)])

  const isLoading = settled !== request

  const refresh = useCallback(() => {
    setRequest((current) => ({ ...current, token: current.token + 1 }))
  }, [])

  const setFilters = useCallback((filters: AlertListFilters) => {
    setRequest((current) => ({ page: 1, filters, token: current.token + 1 }))
  }, [])

  const totalPages = data?.total_pages ?? 0
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

  return useMemo(
    () => ({ data, failure, isLoading, page: request.page, filters: request.filters, nextPage, previousPage, refresh, setFilters }),
    [data, failure, isLoading, request.page, request.filters, nextPage, previousPage, refresh, setFilters],
  )
}

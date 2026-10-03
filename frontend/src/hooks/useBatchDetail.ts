import { useCallback, useEffect, useMemo, useState } from 'react'
import { getDetectionBatch } from '../services'
import type { DetectionBatch } from '../types/detection'
import { describeLoadFailure, type LoadFailure } from '../utils/loadFailure'

export interface BatchDetail {
  data: DetectionBatch | null
  failure: LoadFailure | null
  isLoading: boolean
  /** Re-requests the current batch. No-op while `batchId` is null or a request is already in flight. */
  refresh: () => void
}

interface Settled {
  batchId: string
  token: number
}

/**
 * Loads one persisted batch by id. Presentation-free, mirroring
 * useDetectionBatches: owns the request lifecycle and retry, nothing else.
 *
 * Passing `batchId: null` means "nothing selected": no request is made, and
 * `data`/`failure` are derived as empty at render time rather than reset via
 * an effect. The same derivation also masks a previous batch's still-settled
 * result while a newly selected batch is loading, so callers never see data
 * for the wrong id.
 */
export function useBatchDetail(batchId: string | null): BatchDetail {
  const [token, setToken] = useState(0)
  const [settled, setSettled] = useState<Settled | null>(null)
  const [data, setData] = useState<DetectionBatch | null>(null)
  const [failure, setFailure] = useState<LoadFailure | null>(null)

  useEffect(() => {
    if (!batchId) return undefined

    const controller = new AbortController()
    const current: Settled = { batchId, token }

    getDetectionBatch(batchId, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return
        setData(result)
        setFailure(null)
        setSettled(current)
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setFailure(describeLoadFailure(error))
        setSettled(current)
      })

    return () => controller.abort()
  }, [batchId, token])

  const isCurrent = batchId !== null && settled !== null && settled.batchId === batchId
  const isLoading = batchId !== null && (!isCurrent || settled.token !== token)

  const refresh = useCallback(() => {
    if (!batchId || isLoading) return
    setToken((current) => current + 1)
  }, [batchId, isLoading])

  return useMemo(() => {
    // Not `isCurrent` covers both "nothing selected" and "a different batch
    // than the one last settled" — in both cases, data/failure belong to a
    // batch that is not the one currently requested, so they are hidden.
    if (!isCurrent) return { data: null, failure: null, isLoading, refresh }
    return { data, failure, isLoading, refresh }
  }, [isCurrent, data, failure, isLoading, refresh])
}

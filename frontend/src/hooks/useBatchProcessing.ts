import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { startBatchProcessing } from '../services'
import { describeLoadFailure, type LoadFailure } from '../utils/loadFailure'

export interface BatchProcessingAction {
  isStarting: boolean
  /** Why the last attempt to start processing failed, if it did. */
  failure: LoadFailure | null
  start: () => void
}

/**
 * Owns the "start processing" action for one batch: calls the real endpoint,
 * tracks whether a request is in flight, and reports a clean failure. It does
 * not hold the batch's own data — on success the caller re-fetches the batch
 * (e.g. via useBatchDetail's `refresh`) so there is one source of truth for
 * what the batch's status actually is.
 */
export function useBatchProcessing(batchId: string, onStarted: () => void): BatchProcessingAction {
  const [isStarting, setIsStarting] = useState(false)
  const [failure, setFailure] = useState<LoadFailure | null>(null)
  const inFlightRef = useRef(false)
  const onStartedRef = useRef(onStarted)

  useEffect(() => {
    onStartedRef.current = onStarted
  }, [onStarted])

  const start = useCallback(() => {
    if (inFlightRef.current) return
    inFlightRef.current = true
    setIsStarting(true)
    setFailure(null)

    startBatchProcessing(batchId)
      .then(() => {
        onStartedRef.current()
      })
      .catch((error: unknown) => {
        setFailure(describeLoadFailure(error))
      })
      .finally(() => {
        inFlightRef.current = false
        setIsStarting(false)
      })
  }, [batchId])

  return useMemo(() => ({ isStarting, failure, start }), [isStarting, failure, start])
}

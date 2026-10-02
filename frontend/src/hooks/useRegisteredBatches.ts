import { useCallback, useMemo, useState } from 'react'
import type { UploadBatchResponse } from '../types/detection'

export interface RegisteredBatches {
  /** Batches registered in this browser session, newest first. */
  batches: UploadBatchResponse[]
  add: (batch: UploadBatchResponse) => void
}

/**
 * In-memory list of batches created during this session. There is no list
 * endpoint yet, so this is what lets the UI show a batch the moment it is
 * registered; stored batch history arrives with a later step.
 */
export function useRegisteredBatches(): RegisteredBatches {
  const [batches, setBatches] = useState<UploadBatchResponse[]>([])

  const add = useCallback((batch: UploadBatchResponse) => {
    setBatches((current) => [batch, ...current.filter((existing) => existing.batch_id !== batch.batch_id)])
  }, [])

  return useMemo(() => ({ batches, add }), [batches, add])
}

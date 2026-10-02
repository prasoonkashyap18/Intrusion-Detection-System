import type { ProcessingStatus } from './security'

/**
 * Response of `POST /api/v1/detection/upload`: a newly registered batch.
 * The traffic has NOT been analyzed — a new batch is `pending` with zero
 * processed and failed records.
 */
export interface UploadBatchResponse {
  batch_id: string
  filename: string
  status: ProcessingStatus
  total_records: number
  processed_records: number
  failed_records: number
  /** ISO 8601 timestamp in UTC. */
  created_at: string
}

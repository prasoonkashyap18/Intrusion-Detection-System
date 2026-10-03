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

/** A persisted batch as listed by `GET /api/v1/detection/batches`. */
export interface DetectionBatch extends UploadBatchResponse {
  /** ISO 8601 UTC timestamp; null until a processing step finishes the batch. */
  completed_at: string | null
}

export interface Pagination {
  /** 1-based. */
  page: number
  page_size: number
  total_items: number
  /** 0 when there are no items. */
  total_pages: number
}

/** One page of batches, newest first. An empty collection is a normal response. */
export interface DetectionBatchListResponse extends Pagination {
  items: DetectionBatch[]
}

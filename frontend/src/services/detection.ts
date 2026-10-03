import type { DetectionBatch, DetectionBatchListResponse, UploadBatchResponse } from '../types/detection'
import type { ProcessingStatus } from '../types/security'
import { request } from './api'
import { API_V1_PREFIX, UPLOAD_TIMEOUT_MS } from './config'

const STATUSES: readonly ProcessingStatus[] = ['pending', 'processing', 'completed', 'failed']

function isCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

function isUploadBatchResponse(value: unknown): value is UploadBatchResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    'batch_id' in value &&
    typeof value.batch_id === 'string' &&
    'filename' in value &&
    typeof value.filename === 'string' &&
    'status' in value &&
    typeof value.status === 'string' &&
    (STATUSES as readonly string[]).includes(value.status) &&
    'total_records' in value &&
    isCount(value.total_records) &&
    'processed_records' in value &&
    isCount(value.processed_records) &&
    'failed_records' in value &&
    isCount(value.failed_records) &&
    'created_at' in value &&
    typeof value.created_at === 'string'
  )
}

function isDetectionBatch(value: unknown): value is DetectionBatch {
  return (
    isUploadBatchResponse(value) &&
    'completed_at' in value &&
    (value.completed_at === null || typeof value.completed_at === 'string')
  )
}

function isDetectionBatchListResponse(value: unknown): value is DetectionBatchListResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    'items' in value &&
    Array.isArray(value.items) &&
    value.items.every(isDetectionBatch) &&
    'page' in value &&
    isCount(value.page) &&
    'page_size' in value &&
    isCount(value.page_size) &&
    'total_items' in value &&
    isCount(value.total_items) &&
    'total_pages' in value &&
    isCount(value.total_pages)
  )
}

/**
 * POST /api/v1/detection/upload — sends the CSV as multipart/form-data.
 * Registers a pending batch; it does not analyze the traffic.
 */
export function uploadDetectionCsv(file: File, signal?: AbortSignal): Promise<UploadBatchResponse> {
  const body = new FormData()
  body.append('file', file, file.name)

  return request(`${API_V1_PREFIX}/detection/upload`, {
    method: 'POST',
    body,
    validate: isUploadBatchResponse,
    signal,
    timeoutMs: UPLOAD_TIMEOUT_MS,
  })
}

/** GET /api/v1/detection/batches — one page of persisted batches, newest first. */
export function getDetectionBatches(page: number, pageSize: number, signal?: AbortSignal): Promise<DetectionBatchListResponse> {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })

  return request(`${API_V1_PREFIX}/detection/batches?${query.toString()}`, {
    validate: isDetectionBatchListResponse,
    signal,
  })
}

/** GET /api/v1/detection/batches/{batchId} — one persisted batch by id. */
export function getDetectionBatch(batchId: string, signal?: AbortSignal): Promise<DetectionBatch> {
  return request(`${API_V1_PREFIX}/detection/batches/${encodeURIComponent(batchId)}`, {
    validate: isDetectionBatch,
    signal,
  })
}

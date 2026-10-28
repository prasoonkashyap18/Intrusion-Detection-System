import type {
  BatchProcessingResponse,
  DetectionBatch,
  DetectionBatchListResponse,
  DetectionResult,
  DetectionResultListResponse,
  PredictionName,
  UploadBatchResponse,
} from '../types/detection'
import type { ProcessingStatus } from '../types/security'
import { request } from './api'
import { API_V1_PREFIX, UPLOAD_TIMEOUT_MS } from './config'

const STATUSES: readonly ProcessingStatus[] = ['pending', 'processing', 'completed', 'failed']
const PREDICTION_NAMES: readonly PredictionName[] = ['benign', 'attack']

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

function isBatchProcessingResponse(value: unknown): value is BatchProcessingResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    'batch_id' in value &&
    typeof value.batch_id === 'string' &&
    'status' in value &&
    typeof value.status === 'string' &&
    (STATUSES as readonly string[]).includes(value.status) &&
    'message' in value &&
    typeof value.message === 'string'
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

/** `null` or a finite number in `[0, 1]` — never coerced from a missing probability. */
function isProbabilityOrNull(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1)
}

function isDetectionResult(value: unknown): value is DetectionResult {
  return (
    typeof value === 'object' &&
    value !== null &&
    'id' in value &&
    typeof value.id === 'string' &&
    'batch_id' in value &&
    typeof value.batch_id === 'string' &&
    'model_id' in value &&
    typeof value.model_id === 'string' &&
    'model_name' in value &&
    typeof value.model_name === 'string' &&
    'model_version' in value &&
    typeof value.model_version === 'string' &&
    'row_number' in value &&
    isCount(value.row_number) &&
    'predicted_label' in value &&
    (value.predicted_label === 0 || value.predicted_label === 1) &&
    'prediction_name' in value &&
    typeof value.prediction_name === 'string' &&
    (PREDICTION_NAMES as readonly string[]).includes(value.prediction_name) &&
    'attack_probability' in value &&
    isProbabilityOrNull(value.attack_probability) &&
    'created_at' in value &&
    typeof value.created_at === 'string'
  )
}

function isDetectionResultListResponse(value: unknown): value is DetectionResultListResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    'items' in value &&
    Array.isArray(value.items) &&
    value.items.every(isDetectionResult) &&
    'batch_id' in value &&
    typeof value.batch_id === 'string' &&
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

/**
 * POST /api/v1/detection/batches/{batchId}/process — claims a pending batch
 * and starts the processing boundary. Performs no analysis: the resulting
 * status is `processing` or `failed`, never a claim of completed analysis.
 */
export function startBatchProcessing(batchId: string, signal?: AbortSignal): Promise<BatchProcessingResponse> {
  return request(`${API_V1_PREFIX}/detection/batches/${encodeURIComponent(batchId)}/process`, {
    method: 'POST',
    validate: isBatchProcessingResponse,
    signal,
  })
}

/**
 * GET /api/v1/detection/batches/{batchId}/results — one page of this batch's
 * own persisted predictions, ordered by row_number. Read-only: never runs or
 * re-runs inference.
 */
export function getDetectionResults(
  batchId: string,
  page: number,
  pageSize: number,
  signal?: AbortSignal,
): Promise<DetectionResultListResponse> {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })

  return request(`${API_V1_PREFIX}/detection/batches/${encodeURIComponent(batchId)}/results?${query.toString()}`, {
    validate: isDetectionResultListResponse,
    signal,
  })
}

/** GET /api/v1/detection/results/{resultId} — one persisted detection result by id. */
export function getDetectionResult(resultId: string, signal?: AbortSignal): Promise<DetectionResult> {
  return request(`${API_V1_PREFIX}/detection/results/${encodeURIComponent(resultId)}`, {
    validate: isDetectionResult,
    signal,
  })
}

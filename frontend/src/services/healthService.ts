import type { ApiHealthResponse } from '../types/api'
import { getJson } from './apiClient'
import { API_V1_PREFIX } from './config'

const HEALTH_TIMEOUT_MS = 5_000

function isApiHealthResponse(value: unknown): value is ApiHealthResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    'status' in value &&
    typeof value.status === 'string' &&
    'service' in value &&
    typeof value.service === 'string'
  )
}

export function fetchApiHealth(signal?: AbortSignal): Promise<ApiHealthResponse> {
  return getJson(`${API_V1_PREFIX}/health`, isApiHealthResponse, {
    signal,
    timeoutMs: HEALTH_TIMEOUT_MS,
  })
}

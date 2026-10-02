import type { HealthResponse } from '../types/api'
import { getJson } from './api'
import { API_V1_PREFIX } from './config'

/** Shorter than the default: an unreachable backend should surface quickly in the UI. */
const HEALTH_TIMEOUT_MS = 5_000

function isHealthResponse(value: unknown): value is HealthResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    'status' in value &&
    typeof value.status === 'string' &&
    'service' in value &&
    typeof value.service === 'string'
  )
}

/** GET /api/v1/health — the only endpoint the backend currently exposes. */
export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return getJson(`${API_V1_PREFIX}/health`, isHealthResponse, { signal, timeoutMs: HEALTH_TIMEOUT_MS })
}

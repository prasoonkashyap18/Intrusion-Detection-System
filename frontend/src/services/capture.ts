import type { CaptureSession, CaptureSessionListResponse, InterfaceListResponse } from '../types/capture'
import { request } from './api'
import { API_V1_PREFIX } from './config'

const STATUSES = ['running', 'stopped', 'failed'] as const

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

function isCaptureSession(value: unknown): value is CaptureSession {
  return (
    typeof value === 'object' &&
    value !== null &&
    'id' in value &&
    typeof value.id === 'string' &&
    'batch_id' in value &&
    typeof value.batch_id === 'string' &&
    'interface_name' in value &&
    typeof value.interface_name === 'string' &&
    'status' in value &&
    typeof value.status === 'string' &&
    (STATUSES as readonly string[]).includes(value.status) &&
    'stats' in value &&
    typeof value.stats === 'object' &&
    value.stats !== null &&
    'started_at' in value &&
    typeof value.started_at === 'string' &&
    'stopped_at' in value &&
    isNullableString(value.stopped_at) &&
    'error_message' in value &&
    isNullableString(value.error_message)
  )
}

function isCaptureSessionListResponse(value: unknown): value is CaptureSessionListResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    'items' in value &&
    Array.isArray(value.items) &&
    value.items.every(isCaptureSession)
  )
}

function isInterfaceListResponse(value: unknown): value is InterfaceListResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    'interfaces' in value &&
    Array.isArray(value.interfaces) &&
    value.interfaces.every((item) => typeof item === 'string')
  )
}

/** GET /api/v1/capture/interfaces */
export function getCaptureInterfaces(signal?: AbortSignal): Promise<InterfaceListResponse> {
  return request(`${API_V1_PREFIX}/capture/interfaces`, { validate: isInterfaceListResponse, signal })
}

/** GET /api/v1/capture/sessions */
export function getCaptureSessions(signal?: AbortSignal): Promise<CaptureSessionListResponse> {
  return request(`${API_V1_PREFIX}/capture/sessions`, { validate: isCaptureSessionListResponse, signal })
}

/** GET /api/v1/capture/sessions/{sessionId} */
export function getCaptureSession(sessionId: string, signal?: AbortSignal): Promise<CaptureSession> {
  return request(`${API_V1_PREFIX}/capture/sessions/${encodeURIComponent(sessionId)}`, {
    validate: isCaptureSession,
    signal,
  })
}

/** POST /api/v1/capture/sessions — requires Security Analyst or Administrator. */
export function startCaptureSession(
  interfaceName: string,
  bpfFilter?: string,
  signal?: AbortSignal,
): Promise<CaptureSession> {
  return request(`${API_V1_PREFIX}/capture/sessions`, {
    method: 'POST',
    body: bpfFilter ? { interface: interfaceName, bpf_filter: bpfFilter } : { interface: interfaceName },
    validate: isCaptureSession,
    signal,
  })
}

/** POST /api/v1/capture/sessions/{sessionId}/stop — requires Security Analyst or Administrator. */
export function stopCaptureSession(sessionId: string, signal?: AbortSignal): Promise<CaptureSession> {
  return request(`${API_V1_PREFIX}/capture/sessions/${encodeURIComponent(sessionId)}/stop`, {
    method: 'POST',
    validate: isCaptureSession,
    signal,
  })
}

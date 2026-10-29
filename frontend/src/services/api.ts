import { ApiError } from './apiError'
import { getAuthToken, notifyUnauthorized } from './authToken'
import { API_BASE_URL, DEFAULT_TIMEOUT_MS } from './config'

type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

/** Narrows an unknown JSON body to `T`, so callers never receive unchecked data. */
export type ResponseValidator<T> = (value: unknown) => value is T

export interface RequestOptions<T> {
  /** Defaults to GET. */
  method?: HttpMethod
  /**
   * Request body. Plain objects are JSON-encoded; FormData/Blob and other
   * BodyInit values are passed through untouched (e.g. a future CSV upload).
   */
  body?: BodyInit | Record<string, unknown> | unknown[]
  validate: ResponseValidator<T>
  headers?: Record<string, string>
  /** Cancels the request, e.g. from a React effect cleanup. */
  signal?: AbortSignal
  timeoutMs?: number
}

function isBodyInit(body: unknown): body is BodyInit {
  return (
    typeof body === 'string' ||
    body instanceof FormData ||
    body instanceof Blob ||
    body instanceof URLSearchParams ||
    body instanceof ArrayBuffer
  )
}

const MAX_DETAIL_LENGTH = 300

/** Extracts the backend's `{ error, message }` explanation, ignoring any other body shape. */
async function readErrorDetail(response: Response): Promise<string | null> {
  try {
    const body: unknown = await response.json()
    if (typeof body === 'object' && body !== null && 'message' in body && typeof body.message === 'string') {
      return body.message.trim().slice(0, MAX_DETAIL_LENGTH) || null
    }
  } catch {
    // Not JSON (e.g. a proxy error page); callers fall back to a generic message.
  }
  return null
}

/**
 * Single entry point for backend calls: resolves the base URL, applies a
 * timeout, parses JSON, validates the shape, and converts every failure into
 * a structured ApiError.
 *
 * All current and planned endpoints return JSON; a no-content (204) endpoint
 * would need explicit handling here.
 */
export async function request<T>(path: string, options: RequestOptions<T>): Promise<T> {
  const { method = 'GET', body, validate, headers, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = options

  const timeoutSignal = AbortSignal.timeout(timeoutMs)
  const requestSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal

  const sendsJson = body !== undefined && !isBodyInit(body)
  const token = getAuthToken()
  const requestInit: RequestInit = {
    method,
    headers: {
      Accept: 'application/json',
      ...(sendsJson ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    signal: requestSignal,
  }
  if (body !== undefined) requestInit.body = sendsJson ? JSON.stringify(body) : (body as BodyInit)

  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, requestInit)
  } catch (error) {
    // Checking the signals (rather than the error) identifies the cause
    // reliably across browsers and test environments.
    if (timeoutSignal.aborted) {
      throw new ApiError('timeout', `Request timed out after ${timeoutMs} ms`, null, { cause: error })
    }
    if (signal?.aborted) {
      throw new ApiError('aborted', 'Request was cancelled', null, { cause: error })
    }
    throw new ApiError('network_error', 'Could not reach the backend', null, { cause: error })
  }

  if (!response.ok) {
    const detail = await readErrorDetail(response)
    // A 401 means this session's token is missing, expired, or revoked —
    // surface it to AuthProvider so the app returns to a logged-out state
    // (and the login screen) rather than every caller having to notice
    // this status individually.
    if (response.status === 401) notifyUnauthorized()
    throw new ApiError('http_error', `Request failed with status ${response.status}`, response.status, { detail })
  }

  if (response.status === 204) {
    // No body to parse (e.g. POST /auth/logout) -- validated the same
    // way any other response is, so a validator expecting `undefined`
    // (see isVoid below) still gets the shape check this function
    // otherwise always applies.
    if (!validate(undefined)) {
      throw new ApiError('invalid_response', 'Response did not match the expected shape', response.status)
    }
    return undefined as T
  }

  let payload: unknown
  try {
    payload = await response.json()
  } catch (error) {
    throw new ApiError('invalid_response', 'Response was not valid JSON', response.status, { cause: error })
  }

  if (!validate(payload)) {
    throw new ApiError('invalid_response', 'Response did not match the expected shape', response.status)
  }
  return payload
}

/** Validator for an endpoint that returns no body (e.g. a 204). */
export function isVoid(value: unknown): value is undefined {
  return value === undefined
}

/** Convenience wrapper for the common case. Other verbs go through `request`. */
export function getJson<T>(
  path: string,
  validate: ResponseValidator<T>,
  options: Omit<RequestOptions<T>, 'method' | 'body' | 'validate'> = {},
): Promise<T> {
  return request<T>(path, { ...options, method: 'GET', validate })
}

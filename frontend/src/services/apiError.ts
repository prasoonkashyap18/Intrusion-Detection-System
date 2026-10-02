/**
 * Every failure that leaves the service layer is an ApiError, so callers never
 * have to interpret raw fetch/DOM exceptions or show them to users.
 */
export type ApiErrorCode =
  /** The backend responded with a non-2xx status. */
  | 'http_error'
  /** The backend could not be reached (DNS, refused connection, CORS, offline). */
  | 'network_error'
  /** The request exceeded its timeout before the backend responded. */
  | 'timeout'
  /** The backend responded, but the body was not JSON or not the expected shape. */
  | 'invalid_response'
  /** The caller cancelled the request (e.g. a React effect was cleaned up). */
  | 'aborted'

export class ApiError extends Error {
  readonly code: ApiErrorCode
  /** HTTP status when the backend responded, otherwise null. */
  readonly status: number | null

  constructor(code: ApiErrorCode, message: string, status: number | null = null, options?: ErrorOptions) {
    super(message, options)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }

  /** True when the request never produced a usable response from the backend. */
  get isUnreachable(): boolean {
    return this.code === 'network_error' || this.code === 'timeout'
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError
}

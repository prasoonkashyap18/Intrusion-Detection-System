const DEFAULT_API_BASE_URL = 'http://localhost:8000'

function resolveApiBaseUrl(value: string | undefined): string {
  const trimmed = value?.trim()
  return (trimmed || DEFAULT_API_BASE_URL).replace(/\/+$/, '')
}

/**
 * Public backend base URL, from VITE_API_BASE_URL (see .env.example).
 * Bundled into client code and visible in the browser — never a secret.
 */
export const API_BASE_URL = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL)

/** Matches the backend's default `API_V1_PREFIX`. */
export const API_V1_PREFIX = '/api/v1'

/** Requests fail with a `timeout` ApiError rather than hanging if the backend stops responding. */
export const DEFAULT_TIMEOUT_MS = 8_000

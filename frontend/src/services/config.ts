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

/**
 * Largest CSV the UI will offer to upload. Mirrors the backend's default
 * MAX_UPLOAD_MB (50). This is only for fast feedback — the backend enforces
 * its own limit, which is the one that counts.
 */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024

/** Uploads send the whole file, so they get far longer than a health check. */
export const UPLOAD_TIMEOUT_MS = 120_000

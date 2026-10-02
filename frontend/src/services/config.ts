const DEFAULT_API_BASE_URL = 'http://localhost:8000'

function resolveApiBaseUrl(value: string | undefined): string {
  const trimmed = value?.trim()
  return (trimmed || DEFAULT_API_BASE_URL).replace(/\/+$/, '')
}

/** Public backend base URL (from VITE_API_BASE_URL). Bundled into client code — never a secret. */
export const API_BASE_URL = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL)

/** Matches the backend's default `API_V1_PREFIX`. */
export const API_V1_PREFIX = '/api/v1'

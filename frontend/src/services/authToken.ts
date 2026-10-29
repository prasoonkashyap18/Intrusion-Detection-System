/**
 * The current session's bearer token, held in memory and mirrored to
 * `sessionStorage` (cleared when the tab closes — a deliberately shorter
 * lifetime than `localStorage`, consistent with treating this token as a
 * credential rather than ordinary app state). Plain module-level state
 * (not React state) so every service function — not just components
 * inside a provider — can read the current token without prop-drilling it
 * through every call site.
 *
 * `AuthProvider` (see `../contexts/AuthContext`) is the only code that
 * calls `setAuthToken`/`clearAuthToken`; everything else only reads via
 * `getAuthToken`.
 */

const STORAGE_KEY = 'ai-ids.auth-token'

let currentToken: string | null = readStoredToken()

function readStoredToken(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY)
  } catch {
    // sessionStorage can throw in a locked-down environment (e.g. some
    // private-browsing modes) -- treat that the same as "no token yet".
    return null
  }
}

export function getAuthToken(): string | null {
  return currentToken
}

export function setAuthToken(token: string): void {
  currentToken = token
  try {
    sessionStorage.setItem(STORAGE_KEY, token)
  } catch {
    // Persistence is a convenience (surviving a page refresh); losing it
    // never blocks using the token for the rest of this session.
  }
}

export function clearAuthToken(): void {
  currentToken = null
  try {
    sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    // See setAuthToken.
  }
}

type UnauthorizedListener = () => void
let unauthorizedListener: UnauthorizedListener | null = null

/** Registered once by `AuthProvider` so `services/api.ts` can report a
 * 401 response without importing React/context code itself. */
export function setUnauthorizedListener(listener: UnauthorizedListener | null): void {
  unauthorizedListener = listener
}

export function notifyUnauthorized(): void {
  unauthorizedListener?.()
}

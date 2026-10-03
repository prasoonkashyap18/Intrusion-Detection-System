import { isApiError } from '../services'

export interface LoadFailure {
  /** User-facing explanation. Never raw exception text. */
  message: string
  /** True when the backend could not be reached at all, as opposed to answering with an error. */
  unreachable: boolean
}

/** Turns any list-loading error into a clean message and an offline-vs-error decision. */
export function describeLoadFailure(error: unknown): LoadFailure {
  if (isApiError(error)) {
    if (error.isUnreachable) {
      return { message: 'Could not reach the AI-IDS backend. Check that the FastAPI service is running.', unreachable: true }
    }
    if (error.code === 'invalid_response') {
      return { message: 'The server replied in an unexpected format.', unreachable: false }
    }
    if (error.detail) return { message: error.detail, unreachable: false }
  }
  return { message: 'The data could not be loaded. Please try again.', unreachable: false }
}

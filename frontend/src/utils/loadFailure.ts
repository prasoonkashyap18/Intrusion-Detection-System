import { isApiError } from '../services'

export interface LoadFailure {
  /** User-facing explanation. Never raw exception text. */
  message: string
  /** True when the backend could not be reached at all, as opposed to answering with an error. */
  unreachable: boolean
  /** True when the backend answered with 404: the requested resource does not exist, so retrying the same request cannot help. */
  notFound: boolean
}

/** Turns any list/detail-loading error into a clean message and a classification of what kind of failure it was. */
export function describeLoadFailure(error: unknown): LoadFailure {
  if (isApiError(error)) {
    if (error.isUnreachable) {
      return {
        message: 'Could not reach the AI-IDS backend. Check that the FastAPI service is running.',
        unreachable: true,
        notFound: false,
      }
    }
    if (error.code === 'invalid_response') {
      return { message: 'The server replied in an unexpected format.', unreachable: false, notFound: false }
    }
    if (error.detail) return { message: error.detail, unreachable: false, notFound: error.status === 404 }
  }
  return { message: 'The data could not be loaded. Please try again.', unreachable: false, notFound: false }
}

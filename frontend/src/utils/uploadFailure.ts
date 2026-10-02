import { isApiError } from '../services'

export interface UploadFailure {
  /** User-facing explanation. Never raw exception text. */
  message: string
  /** Whether sending the same file again could succeed. */
  retryable: boolean
}

const UNREACHABLE = 'Could not reach the AI-IDS backend. Check that the FastAPI service is running.'

/** Turns any upload error into a clean message and a retry decision. */
export function describeUploadFailure(error: unknown): UploadFailure {
  if (!isApiError(error)) {
    return { message: 'Something went wrong while uploading the file.', retryable: true }
  }

  switch (error.code) {
    case 'network_error':
      return { message: UNREACHABLE, retryable: true }
    case 'timeout':
      return { message: 'The upload took too long and was stopped. Try again, or use a smaller file.', retryable: true }
    case 'invalid_response':
      return {
        message: 'The server replied unexpectedly, so it is unclear whether the batch was registered.',
        retryable: true,
      }
    case 'aborted':
      return { message: 'The upload was cancelled.', retryable: true }
    case 'http_error': {
      // 5xx may be transient; a 4xx means this file was rejected and resending it will not help.
      const serverFault = error.status !== null && error.status >= 500
      const fallback = serverFault
        ? 'The server could not register the upload. Please try again.'
        : 'The server rejected this file.'
      return { message: error.detail ?? fallback, retryable: serverFault }
    }
  }
}

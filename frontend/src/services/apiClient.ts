import { API_BASE_URL } from './config'

const DEFAULT_TIMEOUT_MS = 8_000

export class ApiRequestError extends Error {
  readonly status: number | null

  constructor(message: string, status: number | null, options?: ErrorOptions) {
    super(message, options)
    this.name = 'ApiRequestError'
    this.status = status
  }
}

interface RequestOptions {
  signal?: AbortSignal
  timeoutMs?: number
}

/**
 * GETs JSON from the backend and validates its shape at the network boundary,
 * so callers never receive unchecked data typed as `any`.
 */
export async function getJson<T>(
  path: string,
  isExpectedShape: (value: unknown) => value is T,
  { signal, timeoutMs = DEFAULT_TIMEOUT_MS }: RequestOptions = {},
): Promise<T> {
  const timeoutSignal = AbortSignal.timeout(timeoutMs)
  const requestSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal

  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { Accept: 'application/json' },
      signal: requestSignal,
    })
  } catch (error) {
    throw new ApiRequestError('Network request failed', null, { cause: error })
  }

  if (!response.ok) {
    throw new ApiRequestError(`Request failed with status ${response.status}`, response.status)
  }

  let body: unknown
  try {
    body = await response.json()
  } catch (error) {
    throw new ApiRequestError('Response was not valid JSON', response.status, { cause: error })
  }

  if (!isExpectedShape(body)) {
    throw new ApiRequestError('Response did not match the expected shape', response.status)
  }
  return body
}

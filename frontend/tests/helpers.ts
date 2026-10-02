import { vi } from 'vitest'
import type { UploadBatchResponse } from '@/types/detection'

/** jsdom has no matchMedia; components query it for reduced-motion and pointer type. */
export function stubMatchMedia(prefersReducedMotion = false) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: query.includes('prefers-reduced-motion') ? prefersReducedMotion : false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  )
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

export function batchResponse(overrides: Partial<UploadBatchResponse> = {}): UploadBatchResponse {
  return {
    batch_id: '3f2b8c1e-6a4d-4e0b-9d7a-2c5f1a8b9e30',
    filename: 'traffic.csv',
    status: 'pending',
    total_records: 1234,
    processed_records: 0,
    failed_records: 0,
    created_at: '2026-10-03T08:15:30Z',
    ...overrides,
  }
}

/** A File whose reported size can differ from its content, to test size limits without allocating megabytes. */
export function csvFile(name = 'traffic.csv', options: { size?: number; content?: string } = {}): File {
  const file = new File([options.content ?? 'a,b\n1,2\n'], name, { type: 'text/csv' })
  if (options.size !== undefined) Object.defineProperty(file, 'size', { value: options.size })
  return file
}

export type FetchInit = RequestInit & { body?: FormData }

export function stubFetch(implementation: (input: RequestInfo | URL, init?: FetchInit) => Promise<Response>) {
  const fetchMock = vi.fn(implementation)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** The File sent in a stubbed upload request's multipart body, asserted rather than assumed. */
export function sentFile(init: FetchInit | undefined): File {
  const value = init?.body?.get('file')
  if (!(value instanceof File)) throw new Error('Request did not include a file')
  return value
}

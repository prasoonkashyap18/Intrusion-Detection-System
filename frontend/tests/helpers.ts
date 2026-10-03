import { vi } from 'vitest'
import type { DetectionBatch, UploadBatchResponse } from '@/types/detection'

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

export function batchItem(overrides: Partial<DetectionBatch> = {}): DetectionBatch {
  return { ...batchResponse(), completed_at: null, ...overrides }
}

/**
 * A stateful stand-in for the backend's detection endpoints, used only in
 * tests. It pages newest-first like the real endpoint, and uploads add a batch
 * to the same store, so tests can prove the UI reads persisted data rather
 * than holding its own copy.
 */
export function stubBackend(initial: DetectionBatch[] = []) {
  const store: DetectionBatch[] = [...initial] // newest first
  let uploads = 0

  const fetchMock = stubFetch((input, init) => {
    const url = new URL(String(input))
    const method = init?.method ?? 'GET'
    const detailMatch = /\/detection\/batches\/([^/]+)$/.exec(url.pathname)

    if (url.pathname.endsWith('/detection/batches') && method === 'GET') {
      const page = Number(url.searchParams.get('page'))
      const pageSize = Number(url.searchParams.get('page_size'))
      return Promise.resolve(
        jsonResponse({
          items: store.slice((page - 1) * pageSize, page * pageSize),
          page,
          page_size: pageSize,
          total_items: store.length,
          total_pages: Math.ceil(store.length / pageSize),
        }),
      )
    }

    if (detailMatch && method === 'GET') {
      const batchId = decodeURIComponent(detailMatch[1] ?? '')
      const found = store.find((batch) => batch.batch_id === batchId)
      return Promise.resolve(
        found
          ? jsonResponse(found)
          : jsonResponse({ error: 'batch_not_found', message: 'No batch was found with that ID.' }, 404),
      )
    }

    if (url.pathname.endsWith('/detection/upload') && method === 'POST') {
      uploads += 1
      const created = batchItem({
        batch_id: `00000000-0000-4000-8000-${String(uploads).padStart(12, '0')}`,
        filename: sentFile(init).name,
        total_records: 3,
        created_at: new Date(Date.UTC(2026, 9, 3, 9, uploads)).toISOString(),
      })
      store.unshift(created)
      return Promise.resolve(jsonResponse(created, 201))
    }

    return Promise.resolve(jsonResponse({ error: 'not_found', message: 'Not found.' }, 404))
  })

  return { store, fetchMock }
}

/** `count` batches, newest first: batch-<count> down to batch-1, one minute apart. */
export function manyBatches(count: number): DetectionBatch[] {
  return Array.from({ length: count }, (_, i) => {
    const n = count - i
    return batchItem({
      batch_id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
      filename: `batch-${n}.csv`,
      created_at: new Date(Date.UTC(2026, 9, 3, 8, n)).toISOString(),
    })
  })
}

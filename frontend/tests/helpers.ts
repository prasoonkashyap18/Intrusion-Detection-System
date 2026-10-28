import { vi } from 'vitest'
import type { DetectionBatch, DetectionResult, UploadBatchResponse } from '@/types/detection'

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

export function resultItem(overrides: Partial<DetectionResult> = {}): DetectionResult {
  return {
    id: '7a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d',
    batch_id: '3f2b8c1e-6a4d-4e0b-9d7a-2c5f1a8b9e30',
    model_id: 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d',
    model_name: 'ai-ids-baseline',
    model_version: '20261003T000000Z',
    row_number: 1,
    predicted_label: 0,
    prediction_name: 'benign',
    attack_probability: 0.12,
    created_at: '2026-10-03T08:15:30Z',
    ...overrides,
  }
}

/** `count` results for `batchId`, row 1..count, one second apart. */
export function manyResults(batchId: string, count: number): DetectionResult[] {
  return Array.from({ length: count }, (_, i) =>
    resultItem({
      id: `00000000-0000-4000-9000-${String(i + 1).padStart(12, '0')}`,
      batch_id: batchId,
      row_number: i + 1,
      created_at: new Date(Date.UTC(2026, 9, 3, 8, 0, i + 1)).toISOString(),
    }),
  )
}

/**
 * A stateful stand-in for the backend's detection endpoints, used only in
 * tests. It pages newest-first like the real endpoint, and uploads add a batch
 * to the same store, so tests can prove the UI reads persisted data rather
 * than holding its own copy.
 */
export function stubBackend(initial: DetectionBatch[] = [], initialResults: DetectionResult[] = []) {
  const store: DetectionBatch[] = [...initial] // newest first
  const resultsStore: DetectionResult[] = [...initialResults]
  let uploads = 0

  const fetchMock = stubFetch((input, init) => {
    const url = new URL(String(input))
    const method = init?.method ?? 'GET'
    const detailMatch = /\/detection\/batches\/([^/]+)$/.exec(url.pathname)
    const processMatch = /\/detection\/batches\/([^/]+)\/process$/.exec(url.pathname)
    const resultsListMatch = /\/detection\/batches\/([^/]+)\/results$/.exec(url.pathname)
    const resultMatch = /\/detection\/results\/([^/]+)$/.exec(url.pathname)

    if (resultsListMatch && method === 'GET') {
      const batchId = decodeURIComponent(resultsListMatch[1] ?? '')
      if (!store.some((batch) => batch.batch_id === batchId)) {
        return Promise.resolve(jsonResponse({ error: 'batch_not_found', message: 'No batch was found with that ID.' }, 404))
      }
      const page = Number(url.searchParams.get('page')) || 1
      const pageSize = Number(url.searchParams.get('page_size')) || 20
      const matching = resultsStore.filter((result) => result.batch_id === batchId)
      return Promise.resolve(
        jsonResponse({
          items: matching.slice((page - 1) * pageSize, page * pageSize),
          batch_id: batchId,
          page,
          page_size: pageSize,
          total_items: matching.length,
          total_pages: Math.ceil(matching.length / pageSize),
        }),
      )
    }

    if (resultMatch && method === 'GET') {
      const resultId = decodeURIComponent(resultMatch[1] ?? '')
      const found = resultsStore.find((result) => result.id === resultId)
      return Promise.resolve(
        found
          ? jsonResponse(found)
          : jsonResponse({ error: 'detection_result_not_found', message: 'No detection result was found with that ID.' }, 404),
      )
    }

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

    if (processMatch && method === 'POST') {
      const batchId = decodeURIComponent(processMatch[1] ?? '')
      const index = store.findIndex((batch) => batch.batch_id === batchId)
      if (index === -1) {
        return Promise.resolve(jsonResponse({ error: 'batch_not_found', message: 'No batch was found with that ID.' }, 404))
      }
      const batch = store[index] as DetectionBatch
      if (batch.status !== 'pending') {
        return Promise.resolve(
          jsonResponse(
            {
              error: 'invalid_batch_state',
              message: `Batch is '${batch.status}' and cannot be started; only a pending batch can be processed.`,
            },
            409,
          ),
        )
      }
      store[index] = { ...batch, status: 'processing' }
      return Promise.resolve(jsonResponse({ batch_id: batchId, status: 'processing', message: 'Batch processing started.' }))
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

  return { store, resultsStore, fetchMock }
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

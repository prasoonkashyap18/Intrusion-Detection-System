/**
 * Tests for the detection upload service and the error detail the API client
 * now extracts from backend error bodies. `fetch` is stubbed throughout.
 */

import { describe, expect, it } from 'vitest'
import { getDetectionBatches, uploadDetectionCsv } from '@/services'
import { request } from '@/services/api'
import { batchItem, batchResponse, csvFile, jsonResponse, sentFile, stubFetch } from './helpers'

describe('uploadDetectionCsv', () => {
  it('posts the file as multipart form data under the `file` field', async () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse(batchResponse(), 201)))

    await uploadDetectionCsv(csvFile('flows.csv'))

    const [url, init] = fetchMock.mock.calls[0] ?? []
    expect(String(url)).toMatch(/\/api\/v1\/detection\/upload$/)
    expect(init?.method).toBe('POST')
    expect(init?.body).toBeInstanceOf(FormData)
    expect(sentFile(init).name).toBe('flows.csv')
  })

  it('returns the typed batch exactly as the backend reported it', async () => {
    stubFetch(() => Promise.resolve(jsonResponse(batchResponse({ total_records: 42 }), 201)))

    await expect(uploadDetectionCsv(csvFile())).resolves.toEqual(batchResponse({ total_records: 42 }))
  })

  it.each([
    ['a missing field', { ...batchResponse(), total_records: undefined }],
    ['an unknown status', { ...batchResponse(), status: 'archived' }],
    ['a negative count', { ...batchResponse(), failed_records: -1 }],
    ['a fractional count', { ...batchResponse(), processed_records: 1.5 }],
    ['a non-string id', { ...batchResponse(), batch_id: 7 }],
    ['a non-object body', 'created'],
  ])('rejects a response with %s as invalid', async (_label, body) => {
    stubFetch(() => Promise.resolve(jsonResponse(body, 201)))

    await expect(uploadDetectionCsv(csvFile())).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('fails with an unreachable-backend error when the network is down', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))

    await expect(uploadDetectionCsv(csvFile())).rejects.toMatchObject({ code: 'network_error' })
  })

  it('can be cancelled by the caller', async () => {
    stubFetch((_input, init) => {
      const signal = init?.signal
      return new Promise((_resolve, reject) => signal?.addEventListener('abort', () => reject(signal.reason)))
    })
    const controller = new AbortController()

    const pending = uploadDetectionCsv(csvFile(), controller.signal).catch((error: unknown) => error)
    controller.abort()

    expect(await pending).toMatchObject({ code: 'aborted' })
  })
})

describe('request — backend error detail', () => {
  const isAnything = (value: unknown): value is unknown => value !== undefined

  async function failure(response: Response) {
    stubFetch(() => Promise.resolve(response))
    return request('/x', { validate: isAnything }).catch((error: unknown) => error)
  }

  it("exposes the backend's user-facing message as `detail`", async () => {
    const error = await failure(jsonResponse({ error: 'file_too_large', message: 'Uploaded file is too large.' }, 413))

    expect(error).toMatchObject({ code: 'http_error', status: 413, detail: 'Uploaded file is too large.' })
  })

  it('leaves detail empty when the body is not the expected shape', async () => {
    expect(await failure(jsonResponse({ detail: [{ msg: 'x' }] }, 422))).toMatchObject({ detail: null })
    expect(await failure(jsonResponse(['nope'], 400))).toMatchObject({ detail: null })
  })

  it('leaves detail empty for a non-JSON error page', async () => {
    const error = await failure(new Response('<html>Bad gateway</html>', { status: 502 }))

    expect(error).toMatchObject({ code: 'http_error', status: 502, detail: null })
  })

  it('caps very long messages so the UI cannot be flooded', async () => {
    const error = await failure(jsonResponse({ error: 'x', message: 'a'.repeat(5000) }, 400))

    expect(error).toMatchObject({ detail: 'a'.repeat(300) })
  })

  it('does not treat a blank message as detail', async () => {
    expect(await failure(jsonResponse({ error: 'x', message: '   ' }, 400))).toMatchObject({ detail: null })
  })
})

describe('getDetectionBatches', () => {
  const page = (items: unknown[] = [batchItem()], extra: Record<string, unknown> = {}) => ({
    items,
    page: 1,
    page_size: 10,
    total_items: items.length,
    total_pages: items.length ? 1 : 0,
    ...extra,
  })

  it('issues a GET with the page and page size in the query string', async () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse(page())))

    await getDetectionBatches(3, 25)

    const [url, init] = fetchMock.mock.calls[0] ?? []
    const parsed = new URL(String(url))
    expect(parsed.pathname).toBe('/api/v1/detection/batches')
    expect(parsed.searchParams.get('page')).toBe('3')
    expect(parsed.searchParams.get('page_size')).toBe('25')
    expect(init?.method).toBe('GET')
    expect(init?.body).toBeUndefined()
  })

  it('returns the typed page exactly as reported, including an empty collection', async () => {
    stubFetch(() => Promise.resolve(jsonResponse(page([]))))

    await expect(getDetectionBatches(1, 10)).resolves.toEqual(page([]))
  })

  it('accepts a completed batch with a completion time', async () => {
    const done = batchItem({ status: 'completed', completed_at: '2026-10-03T09:00:00Z' })
    stubFetch(() => Promise.resolve(jsonResponse(page([done]))))

    await expect(getDetectionBatches(1, 10)).resolves.toMatchObject({ items: [done] })
  })

  it.each([
    ['a missing items array', { page: 1, page_size: 10, total_items: 0, total_pages: 0 }],
    ['an item missing completed_at', page([{ ...batchResponse() }])],
    ['an item with an unknown status', page([{ ...batchItem(), status: 'archived' }])],
    ['a non-numeric total', page([], { total_items: 'many' })],
    ['a negative page count', page([], { total_pages: -1 })],
  ])('rejects %s as an invalid response', async (_label, body) => {
    stubFetch(() => Promise.resolve(jsonResponse(body)))

    await expect(getDetectionBatches(1, 10)).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('surfaces a server error with the backend explanation', async () => {
    stubFetch(() =>
      Promise.resolve(jsonResponse({ error: 'batches_unavailable', message: 'Unable to load detection batches.' }, 500)),
    )

    await expect(getDetectionBatches(1, 10)).rejects.toMatchObject({
      code: 'http_error',
      status: 500,
      detail: 'Unable to load detection batches.',
    })
  })

  it('fails with a network error when the backend is unreachable', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))

    await expect(getDetectionBatches(1, 10)).rejects.toMatchObject({ code: 'network_error' })
  })
})

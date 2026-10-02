/**
 * Tests for the API client's contract: how it builds requests and how every
 * failure mode is converted into a structured ApiError.
 *
 * `fetch` is stubbed throughout — no test contacts a real backend.
 */

import { describe, expect, it, vi } from 'vitest'
import { getJson, request } from '@/services/api'
import { ApiError } from '@/services/apiError'
import { API_BASE_URL } from '@/services/config'

interface Greeting {
  message: string
}

const isGreeting = (value: unknown): value is Greeting =>
  typeof value === 'object' && value !== null && 'message' in value && typeof value.message === 'string'

function stubFetch(implementation: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>) {
  const fetchMock = vi.fn(implementation)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** Stands in for a backend that accepts the request and then stops responding. */
function stubUnresponsiveFetch() {
  stubFetch((_input, init) => {
    const signal = init?.signal
    return new Promise((_resolve, reject) => {
      signal?.addEventListener('abort', () => reject(signal.reason))
    })
  })
}

describe('request', () => {
  it('resolves with the validated body and calls the absolute backend URL', async () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse({ message: 'hello' })))

    await expect(getJson('/api/v1/greeting', isGreeting)).resolves.toEqual({ message: 'hello' })
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`${API_BASE_URL}/api/v1/greeting`)
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: 'GET' })
  })

  it('JSON-encodes plain object bodies and sets the content type', async () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse({ message: 'created' })))

    await request('/api/v1/greeting', { method: 'POST', body: { name: 'ai-ids' }, validate: isGreeting })

    const init = fetchMock.mock.calls[0]?.[1]
    expect(init?.method).toBe('POST')
    expect(init?.body).toBe('{"name":"ai-ids"}')
    expect(init?.headers).toMatchObject({ 'Content-Type': 'application/json' })
  })

  it('passes FormData through untouched so the browser can set its boundary', async () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse({ message: 'uploaded' })))
    const form = new FormData()
    form.append('file', new Blob(['a,b,c'], { type: 'text/csv' }), 'flows.csv')

    await request('/api/v1/upload', { method: 'POST', body: form, validate: isGreeting })

    const init = fetchMock.mock.calls[0]?.[1]
    expect(init?.body).toBe(form)
    expect(init?.headers).not.toHaveProperty('Content-Type')
  })

  it('reports a non-2xx response as an http_error carrying the status', async () => {
    stubFetch(() => Promise.resolve(jsonResponse({ detail: 'boom' }, 500)))

    const error = await getJson('/api/v1/greeting', isGreeting).catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ code: 'http_error', status: 500 })
    expect((error as ApiError).isUnreachable).toBe(false)
  })

  it('reports an unreachable backend as a network_error', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))

    const error = await getJson('/api/v1/greeting', isGreeting).catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ code: 'network_error', status: null })
    expect((error as ApiError).isUnreachable).toBe(true)
    // The underlying failure is preserved for logging, not shown to users.
    expect((error as ApiError).cause).toBeInstanceOf(TypeError)
  })

  it('reports a non-JSON body as an invalid_response', async () => {
    stubFetch(() => Promise.resolve(new Response('<html>not json</html>', { status: 200 })))

    const error = await getJson('/api/v1/greeting', isGreeting).catch((caught: unknown) => caught)

    expect(error).toMatchObject({ code: 'invalid_response', status: 200 })
  })

  it('reports a body that fails validation as an invalid_response', async () => {
    stubFetch(() => Promise.resolve(jsonResponse({ unexpected: true })))

    const error = await getJson('/api/v1/greeting', isGreeting).catch((caught: unknown) => caught)

    expect(error).toMatchObject({ code: 'invalid_response', status: 200 })
  })

  it('times out instead of hanging when the backend never responds', async () => {
    stubUnresponsiveFetch()

    const error = await getJson('/api/v1/greeting', isGreeting, { timeoutMs: 10 }).catch(
      (caught: unknown) => caught,
    )

    expect(error).toMatchObject({ code: 'timeout' })
    expect((error as ApiError).isUnreachable).toBe(true)
  })

  it('distinguishes a caller-cancelled request from a backend failure', async () => {
    stubUnresponsiveFetch()
    const controller = new AbortController()

    const pending = getJson('/api/v1/greeting', isGreeting, { signal: controller.signal }).catch(
      (caught: unknown) => caught,
    )
    controller.abort()

    expect(await pending).toMatchObject({ code: 'aborted' })
  })

  it('never rejects with a raw fetch error', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))

    await expect(getJson('/api/v1/greeting', isGreeting)).rejects.toBeInstanceOf(ApiError)
  })
})

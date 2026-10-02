/**
 * Tests for the health service: the request it issues and the shape it
 * guarantees to callers. `fetch` is stubbed — no real backend is contacted.
 */

import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/services/apiError'
import { API_BASE_URL } from '@/services/config'
import { getHealth } from '@/services/health'

function stubFetchOnce(body: unknown, status = 200) {
  const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('getHealth', () => {
  it('requests the backend health endpoint', async () => {
    const fetchMock = stubFetchOnce({ status: 'healthy', service: 'ai-ids-api' })

    await getHealth()

    expect(fetchMock.mock.calls[0]?.[0]).toBe(`${API_BASE_URL}/api/v1/health`)
  })

  it('returns the backend payload exactly as reported', async () => {
    stubFetchOnce({ status: 'healthy', service: 'ai-ids-api' })

    await expect(getHealth()).resolves.toEqual({ status: 'healthy', service: 'ai-ids-api' })
  })

  it('does not invent a healthy status when the backend reports something else', async () => {
    stubFetchOnce({ status: 'degraded', service: 'ai-ids-api' })

    await expect(getHealth()).resolves.toMatchObject({ status: 'degraded' })
  })

  it('rejects a payload missing the expected fields', async () => {
    stubFetchOnce({ status: 'healthy' })

    await expect(getHealth()).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('surfaces an unreachable backend as a structured ApiError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )

    const error = await getHealth().catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ code: 'network_error' })
  })
})

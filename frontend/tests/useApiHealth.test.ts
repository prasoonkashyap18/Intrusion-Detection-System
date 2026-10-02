/**
 * Tests for the health-check lifecycle the UI depends on: connecting on first
 * render, then only the state the backend actually reports.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useApiHealth } from '@/hooks/useApiHealth'

function healthResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function stubHealthFetch(implementation: () => Promise<Response>) {
  const fetchMock = vi.fn(implementation)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** Long enough that the interval never fires during a test; each test drives checks explicitly. */
const NO_POLLING = 60_000

describe('useApiHealth', () => {
  it('starts in the connecting state before the first response arrives', () => {
    stubHealthFetch(() => new Promise(() => {}))

    const { result } = renderHook(() => useApiHealth(NO_POLLING))

    expect(result.current.status).toBe('connecting')
    expect(result.current.latencyMs).toBeNull()
    expect(result.current.lastCheckedAt).toBeNull()
  })

  it('reports online with a measured latency when the backend is healthy', async () => {
    stubHealthFetch(() => Promise.resolve(healthResponse({ status: 'healthy', service: 'ai-ids-api' })))

    const { result } = renderHook(() => useApiHealth(NO_POLLING))

    await waitFor(() => expect(result.current.status).toBe('online'))
    expect(result.current.latencyMs).toBeGreaterThanOrEqual(0)
    expect(result.current.lastCheckedAt).toBeInstanceOf(Date)
  })

  it('reports offline when the backend cannot be reached', async () => {
    stubHealthFetch(() => Promise.reject(new TypeError('Failed to fetch')))

    const { result } = renderHook(() => useApiHealth(NO_POLLING))

    await waitFor(() => expect(result.current.status).toBe('offline'))
    expect(result.current.latencyMs).toBeNull()
  })

  it('reports offline when the backend returns an error status', async () => {
    stubHealthFetch(() => Promise.resolve(healthResponse({ detail: 'boom' }, 503)))

    const { result } = renderHook(() => useApiHealth(NO_POLLING))

    await waitFor(() => expect(result.current.status).toBe('offline'))
  })

  it('reports degraded when the backend responds without a healthy status', async () => {
    stubHealthFetch(() => Promise.resolve(healthResponse({ status: 'starting', service: 'ai-ids-api' })))

    const { result } = renderHook(() => useApiHealth(NO_POLLING))

    await waitFor(() => expect(result.current.status).toBe('degraded'))
  })

  it('recovers from offline to online on a re-check once the backend returns', async () => {
    let reachable = false
    const fetchMock = stubHealthFetch(() =>
      reachable
        ? Promise.resolve(healthResponse({ status: 'healthy', service: 'ai-ids-api' }))
        : Promise.reject(new TypeError('Failed to fetch')),
    )

    const { result } = renderHook(() => useApiHealth(NO_POLLING))
    await waitFor(() => expect(result.current.status).toBe('offline'))

    reachable = true
    act(() => result.current.recheck())

    await waitFor(() => expect(result.current.status).toBe('online'))
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('stops checking after unmount so a late response cannot update a gone component', async () => {
    const fetchMock = stubHealthFetch(() =>
      Promise.resolve(healthResponse({ status: 'healthy', service: 'ai-ids-api' })),
    )

    const { result, unmount } = renderHook(() => useApiHealth(NO_POLLING))
    await waitFor(() => expect(result.current.status).toBe('online'))

    unmount()
    await new Promise((resolve) => setTimeout(resolve, 20))

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

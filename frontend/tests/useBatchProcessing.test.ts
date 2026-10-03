/**
 * Tests for the batch-processing action hook: calling the real endpoint,
 * the in-flight guard against duplicate submissions, and failure reporting.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useBatchProcessing } from '@/hooks/useBatchProcessing'
import { jsonResponse, stubFetch } from './helpers'

describe('useBatchProcessing', () => {
  it('starts idle and calls nothing until start() is invoked', () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse({ batch_id: 'batch-1', status: 'processing', message: 'ok' })))

    const { result } = renderHook(() => useBatchProcessing('batch-1', vi.fn()))

    expect(result.current).toMatchObject({ isStarting: false, failure: null })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('posts to the process endpoint for the given batch id', async () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse({ batch_id: 'batch-1', status: 'processing', message: 'ok' })))
    const { result } = renderHook(() => useBatchProcessing('batch-1', vi.fn()))

    act(() => result.current.start())

    await waitFor(() => expect(result.current.isStarting).toBe(false))
    const [url, init] = fetchMock.mock.calls[0] ?? []
    expect(String(url)).toBe('http://localhost:8000/api/v1/detection/batches/batch-1/process')
    expect(init?.method).toBe('POST')
  })

  it('reports isStarting while the request is in flight', async () => {
    let resolveRequest: (() => void) | undefined
    stubFetch(
      () =>
        new Promise((resolve) => {
          resolveRequest = () => resolve(jsonResponse({ batch_id: 'batch-1', status: 'processing', message: 'ok' }))
        }),
    )
    const { result } = renderHook(() => useBatchProcessing('batch-1', vi.fn()))

    act(() => result.current.start())
    expect(result.current.isStarting).toBe(true)

    resolveRequest?.()
    await waitFor(() => expect(result.current.isStarting).toBe(false))
  })

  it('calls onStarted once the backend responds, regardless of the resulting status', async () => {
    stubFetch(() => Promise.resolve(jsonResponse({ batch_id: 'batch-1', status: 'failed', message: 'could not start' })))
    const onStarted = vi.fn()
    const { result } = renderHook(() => useBatchProcessing('batch-1', onStarted))

    act(() => result.current.start())

    await waitFor(() => expect(onStarted).toHaveBeenCalledOnce())
    expect(result.current.failure).toBeNull()
  })

  it('ignores a second start() while one is already in flight', async () => {
    let calls = 0
    const fetchMock = stubFetch(() => {
      calls += 1
      return new Promise(() => {})
    })
    const { result } = renderHook(() => useBatchProcessing('batch-1', vi.fn()))

    act(() => {
      result.current.start()
      result.current.start()
      result.current.start()
    })

    expect(calls).toBe(1)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('reports a clean failure when the batch cannot be started', async () => {
    stubFetch(() =>
      Promise.resolve(
        jsonResponse({ error: 'invalid_batch_state', message: "Batch is 'processing' and cannot be started." }, 409),
      ),
    )
    const { result } = renderHook(() => useBatchProcessing('batch-1', vi.fn()))

    act(() => result.current.start())

    await waitFor(() => expect(result.current.failure).not.toBeNull())
    expect(result.current.failure?.message).toBe("Batch is 'processing' and cannot be started.")
    expect(result.current.isStarting).toBe(false)
  })

  it('reports an unreachable backend in plain language', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))
    const { result } = renderHook(() => useBatchProcessing('batch-1', vi.fn()))

    act(() => result.current.start())

    await waitFor(() => expect(result.current.failure).not.toBeNull())
    expect(result.current.failure?.message).toMatch(/could not reach the ai-ids backend/i)
  })

  it('clears a previous failure on the next attempt', async () => {
    let attempt = 0
    stubFetch(() => {
      attempt += 1
      return attempt === 1
        ? Promise.resolve(jsonResponse({ error: 'invalid_batch_state', message: 'not pending' }, 409))
        : Promise.resolve(jsonResponse({ batch_id: 'batch-1', status: 'processing', message: 'ok' }))
    })
    const { result } = renderHook(() => useBatchProcessing('batch-1', vi.fn()))
    act(() => result.current.start())
    await waitFor(() => expect(result.current.failure).not.toBeNull())

    act(() => result.current.start())

    await waitFor(() => expect(result.current.failure).toBeNull())
  })

  it('allows starting again after a previous attempt finished', async () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse({ batch_id: 'batch-1', status: 'processing', message: 'ok' })))
    const { result } = renderHook(() => useBatchProcessing('batch-1', vi.fn()))
    act(() => result.current.start())
    await waitFor(() => expect(result.current.isStarting).toBe(false))

    act(() => result.current.start())

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  })
})

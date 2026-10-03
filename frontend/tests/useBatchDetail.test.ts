/**
 * Tests for the single-batch loading hook: request lifecycle, the masking of
 * stale data when the selected id changes, the duplicate-refresh guard, and
 * the idle (nothing selected) state.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useBatchDetail } from '@/hooks/useBatchDetail'
import { batchItem, jsonResponse, stubBackend, stubFetch } from './helpers'

describe('useBatchDetail', () => {
  it('makes no request and reports nothing while no batch is selected', () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse(batchItem())))

    const { result } = renderHook(() => useBatchDetail(null))

    expect(result.current).toMatchObject({ data: null, failure: null, isLoading: false })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('starts loading as soon as a batch id is given, then exposes the batch', async () => {
    const target = batchItem({ batch_id: 'batch-1', filename: 'traffic.csv' })
    stubBackend([target])

    const { result } = renderHook(() => useBatchDetail('batch-1'))
    expect(result.current).toMatchObject({ isLoading: true, data: null, failure: null })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data).toEqual(target)
  })

  it('reports a clean failure for an id the backend does not have', async () => {
    stubBackend([])

    const { result } = renderHook(() => useBatchDetail('missing'))

    await waitFor(() => expect(result.current.failure).not.toBeNull())
    expect(result.current.data).toBeNull()
    expect(result.current.failure).toMatchObject({
      message: 'No batch was found with that ID.',
      notFound: true,
      unreachable: false,
    })
  })

  it('distinguishes an unreachable backend from a 404', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))

    const { result } = renderHook(() => useBatchDetail('batch-1'))

    await waitFor(() => expect(result.current.failure).not.toBeNull())
    expect(result.current.failure).toMatchObject({ unreachable: true, notFound: false })
  })

  it('never shows one batch’s data under a different id: switching ids masks the previous result while loading', async () => {
    const first = batchItem({ batch_id: 'batch-1', filename: 'first.csv' })
    const second = batchItem({ batch_id: 'batch-2', filename: 'second.csv' })
    stubBackend([first, second])

    const { result, rerender } = renderHook(({ id }) => useBatchDetail(id), { initialProps: { id: 'batch-1' as string | null } })
    await waitFor(() => expect(result.current.data).toEqual(first))

    let resolveSecond: (() => void) | undefined
    stubFetch(() => new Promise((resolve) => (resolveSecond = () => resolve(jsonResponse(second)))))
    rerender({ id: 'batch-2' })

    // While batch-2 is still loading, batch-1's data must not be shown as if it belonged to batch-2.
    expect(result.current.isLoading).toBe(true)
    expect(result.current.data).toBeNull()

    resolveSecond?.()
    await waitFor(() => expect(result.current.data).toEqual(second))
  })

  it('clears to idle when the selection is cleared', async () => {
    stubBackend([batchItem({ batch_id: 'batch-1' })])
    const { result, rerender } = renderHook(({ id }) => useBatchDetail(id), { initialProps: { id: 'batch-1' as string | null } })
    await waitFor(() => expect(result.current.data).not.toBeNull())

    rerender({ id: null })

    expect(result.current).toMatchObject({ data: null, failure: null, isLoading: false })
  })

  it('ignores refresh while a request is already in flight', async () => {
    let calls = 0
    const fetchMock = stubFetch(() => {
      calls += 1
      return calls === 1 ? Promise.resolve(jsonResponse(batchItem())) : new Promise(() => {})
    })
    const { result } = renderHook(() => useBatchDetail('batch-1'))
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    act(() => result.current.refresh())
    await waitFor(() => expect(result.current.isLoading).toBe(true))
    act(() => {
      result.current.refresh()
      result.current.refresh()
    })

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does nothing when refresh is called with nothing selected', () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse(batchItem())))
    const { result } = renderHook(() => useBatchDetail(null))

    act(() => result.current.refresh())

    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('keeps the previous result visible while refreshing, and clears a stale failure on success', async () => {
    let calls = 0
    stubFetch(() => {
      calls += 1
      if (calls === 2) return Promise.reject(new TypeError('Failed to fetch'))
      return Promise.resolve(jsonResponse(batchItem({ batch_id: 'batch-1' })))
    })
    const { result } = renderHook(() => useBatchDetail('batch-1'))
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    act(() => result.current.refresh())
    await waitFor(() => expect(result.current.failure).not.toBeNull())
    expect(result.current.data).not.toBeNull() // stale-but-same-batch data is kept, not hidden
    expect(result.current.failure).toMatchObject({ unreachable: true })

    act(() => result.current.refresh())
    await waitFor(() => expect(result.current.failure).toBeNull())
  })

  it('aborts the in-flight request when the batch id changes before it resolves', async () => {
    const aborted: string[] = []
    stubFetch((input, init) => {
      const id = String(input).split('/').pop() ?? ''
      init?.signal?.addEventListener('abort', () => aborted.push(id))
      return new Promise(() => {})
    })

    const { rerender, unmount } = renderHook(({ id }) => useBatchDetail(id), { initialProps: { id: 'batch-1' } })
    rerender({ id: 'batch-2' })
    unmount()

    expect(aborted).toEqual(['batch-1', 'batch-2'])
  })
})

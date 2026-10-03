/**
 * Tests for the batch-loading hook on its own: request lifecycle, the
 * duplicate-refresh guard, and keeping data across failures.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useDetectionBatches } from '@/hooks/useDetectionBatches'
import { batchItem, jsonResponse, manyBatches, stubBackend, stubFetch } from './helpers'

const emptyPage = { items: [], page: 1, page_size: 10, total_items: 0, total_pages: 0 }

describe('useDetectionBatches', () => {
  it('starts loading with no data, then exposes the first page', async () => {
    stubBackend(manyBatches(2))

    const { result } = renderHook(() => useDetectionBatches())
    expect(result.current).toMatchObject({ isLoading: true, data: null, failure: null, page: 1 })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data?.items).toHaveLength(2)
    expect(result.current.data?.total_items).toBe(2)
  })

  it('ignores refresh while a request is already in flight', async () => {
    let calls = 0
    const fetchMock = stubFetch(() => {
      calls += 1
      return calls === 1 ? Promise.resolve(jsonResponse(emptyPage)) : new Promise(() => {})
    })
    const { result } = renderHook(() => useDetectionBatches())
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    act(() => result.current.refresh())
    await waitFor(() => expect(result.current.isLoading).toBe(true))
    act(() => {
      result.current.refresh()
      result.current.refresh()
    })

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('keeps the previous data when a later request fails, and clears the failure on success', async () => {
    let calls = 0
    stubFetch(() => {
      calls += 1
      if (calls === 2) return Promise.reject(new TypeError('Failed to fetch'))
      return Promise.resolve(
        jsonResponse({ ...emptyPage, items: [batchItem()], total_items: 1, total_pages: 1 }),
      )
    })
    const { result } = renderHook(() => useDetectionBatches())
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    act(() => result.current.refresh())
    await waitFor(() => expect(result.current.failure).not.toBeNull())
    expect(result.current.data?.items).toHaveLength(1)
    expect(result.current.failure).toMatchObject({ unreachable: true })

    act(() => result.current.refresh())
    await waitFor(() => expect(result.current.failure).toBeNull())
  })

  it('does not page past the ends', async () => {
    stubBackend(manyBatches(15))
    const { result } = renderHook(() => useDetectionBatches())
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    act(() => result.current.previousPage())
    expect(result.current.page).toBe(1)

    act(() => result.current.nextPage())
    await waitFor(() => expect(result.current.data?.page).toBe(2))
    act(() => result.current.nextPage())
    expect(result.current.page).toBe(2)
  })

  it('reloadFirstPage returns to page one and fetches it again', async () => {
    const { fetchMock } = stubBackend(manyBatches(15))
    const { result } = renderHook(() => useDetectionBatches())
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    act(() => result.current.nextPage())
    await waitFor(() => expect(result.current.data?.page).toBe(2))

    act(() => result.current.reloadFirstPage())

    await waitFor(() => expect(result.current.data?.page).toBe(1))
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })
})

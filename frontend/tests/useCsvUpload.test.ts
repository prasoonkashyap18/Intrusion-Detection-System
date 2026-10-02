/**
 * Tests for the upload lifecycle hook: sequencing, the duplicate-submission
 * guard, and behaviour when the component goes away mid-upload.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useCsvUpload } from '@/hooks/useCsvUpload'
import { batchResponse, csvFile, jsonResponse, stubFetch } from './helpers'

describe('useCsvUpload', () => {
  it('starts idle with nothing selected', () => {
    const { result } = renderHook(() => useCsvUpload())

    expect(result.current).toMatchObject({ phase: 'idle', file: null, batch: null, failure: null })
  })

  it('moves to selected for a valid file and stays idle with a message for an invalid one', () => {
    const { result } = renderHook(() => useCsvUpload())

    act(() => result.current.selectFile(csvFile('flows.csv')))
    expect(result.current.phase).toBe('selected')

    act(() => result.current.selectFile(csvFile('flows.txt')))
    expect(result.current).toMatchObject({ phase: 'idle', file: null })
    expect(result.current.validationMessage).toMatch(/\.csv/)
  })

  it('sends only one request however many times upload is called while in flight', () => {
    const fetchMock = stubFetch(() => new Promise(() => {}))
    const { result } = renderHook(() => useCsvUpload())
    act(() => result.current.selectFile(csvFile()))

    act(() => {
      result.current.upload()
      result.current.upload()
      result.current.upload()
    })

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(result.current.phase).toBe('uploading')
  })

  it('does not change the selection while uploading', () => {
    stubFetch(() => new Promise(() => {}))
    const { result } = renderHook(() => useCsvUpload())
    act(() => result.current.selectFile(csvFile('first.csv')))
    act(() => result.current.upload())

    act(() => result.current.selectFile(csvFile('second.csv')))
    act(() => result.current.reset())

    expect(result.current.file?.name).toBe('first.csv')
    expect(result.current.phase).toBe('uploading')
  })

  it('reports success once and passes the batch to onUploaded', async () => {
    stubFetch(() => Promise.resolve(jsonResponse(batchResponse(), 201)))
    const onUploaded = vi.fn()
    const { result } = renderHook(() => useCsvUpload({ onUploaded }))
    act(() => result.current.selectFile(csvFile()))

    act(() => result.current.upload())

    await waitFor(() => expect(result.current.phase).toBe('success'))
    expect(result.current.batch).toEqual(batchResponse())
    expect(onUploaded).toHaveBeenCalledExactlyOnceWith(batchResponse())
  })

  it('allows another upload after a failure', async () => {
    let attempt = 0
    const fetchMock = stubFetch(() => {
      attempt += 1
      return attempt === 1
        ? Promise.reject(new TypeError('Failed to fetch'))
        : Promise.resolve(jsonResponse(batchResponse(), 201))
    })
    const { result } = renderHook(() => useCsvUpload())
    act(() => result.current.selectFile(csvFile()))
    act(() => result.current.upload())
    await waitFor(() => expect(result.current.phase).toBe('error'))
    expect(result.current.failure).toMatchObject({ retryable: true })

    act(() => result.current.upload())

    await waitFor(() => expect(result.current.phase).toBe('success'))
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('cancels the request and never calls back after unmounting mid-upload', async () => {
    let aborted = false
    stubFetch((_input, init) => {
      const signal = init?.signal
      return new Promise((_resolve, reject) =>
        signal?.addEventListener('abort', () => {
          aborted = true
          reject(signal.reason)
        }),
      )
    })
    const onUploaded = vi.fn()
    const { result, unmount } = renderHook(() => useCsvUpload({ onUploaded }))
    act(() => result.current.selectFile(csvFile()))
    act(() => result.current.upload())

    unmount()
    await new Promise((resolve) => setTimeout(resolve, 10))

    expect(aborted).toBe(true)
    expect(onUploaded).not.toHaveBeenCalled()
  })

  it('does nothing when upload is called with no file selected', () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse(batchResponse(), 201)))
    const { result } = renderHook(() => useCsvUpload())

    act(() => result.current.upload())

    expect(fetchMock).not.toHaveBeenCalled()
  })
})

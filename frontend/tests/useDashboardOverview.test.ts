/** Tests for the SOC dashboard overview hook: loading, real measured
 * data, and error reporting -- never a fabricated metric on failure. */

import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useDashboardOverview } from '@/hooks/useDashboardOverview'
import { jsonResponse, stubFetch } from './helpers'

const overview = {
  total_batches: 3,
  total_detection_results: 10,
  total_anomaly_results: 4,
  total_alerts: 2,
  open_alerts: 1,
  alert_severity_distribution: { low: 0, medium: 1, high: 0, critical: 1 },
  active_capture_sessions: 0,
}

describe('useDashboardOverview', () => {
  it('starts loading with no data, then exposes the real metrics', async () => {
    stubFetch(() => Promise.resolve(jsonResponse(overview)))

    const { result } = renderHook(() => useDashboardOverview())
    expect(result.current).toMatchObject({ isLoading: true, data: null, failure: null })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data).toEqual(overview)
  })

  it('reports a failure without fabricating a zeroed overview', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))

    const { result } = renderHook(() => useDashboardOverview())
    await waitFor(() => expect(result.current.failure).not.toBeNull())

    expect(result.current.data).toBeNull()
    expect(result.current.failure).toMatchObject({ unreachable: true })
  })

  it('refresh re-requests the overview', async () => {
    let calls = 0
    const fetchMock = stubFetch(() => {
      calls += 1
      return Promise.resolve(jsonResponse({ ...overview, total_alerts: calls }))
    })
    const { result } = renderHook(() => useDashboardOverview())
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data?.total_alerts).toBe(1)

    act(() => result.current.refresh())
    await waitFor(() => expect(result.current.data?.total_alerts).toBe(2))
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})

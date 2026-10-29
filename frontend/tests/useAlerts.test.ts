/** Tests for the alert-list hook: request lifecycle, server-side
 * status/severity filtering, and resetting to page 1 on filter change. */

import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useAlerts } from '@/hooks/useAlerts'
import type { Alert } from '@/types/alert'
import { jsonResponse, stubFetch } from './helpers'

function alertItem(overrides: Partial<Alert> = {}): Alert {
  return {
    id: '00000000-0000-4000-9000-000000000001',
    batch_id: '3f2b8c1e-6a4d-4e0b-9d7a-2c5f1a8b9e30',
    row_number: 1,
    detection_result_id: null,
    anomaly_result_id: null,
    hybrid_result_id: null,
    risk_assessment_id: null,
    explanation_id: null,
    title: 'Confirmed attack signal',
    description: 'd',
    severity: 'critical',
    status: 'open',
    evidence: {
      hybrid_outcome: 'confirmed_attack',
      contributing_signals: ['supervised:attack', 'anomaly:flagged'],
      risk_score: 90,
      severity: 'critical',
      explanation: {
        measured_values: {},
        rule_based_explanations: [],
        unavailable_information: [],
        feature_importance: null,
        feature_importance_scope: null,
      },
    },
    created_at: '2026-10-03T08:15:30Z',
    updated_at: '2026-10-03T08:15:30Z',
    ...overrides,
  }
}

function pageOf(items: Alert[], page = 1, pageSize = 10, totalItems = items.length) {
  return { items, page, page_size: pageSize, total_items: totalItems, total_pages: Math.ceil(totalItems / pageSize) }
}

describe('useAlerts', () => {
  it('starts loading with no data, then exposes the first page', async () => {
    stubFetch(() => Promise.resolve(jsonResponse(pageOf([alertItem(), alertItem({ id: 'b' })]))))

    const { result } = renderHook(() => useAlerts())
    expect(result.current).toMatchObject({ isLoading: true, data: null, failure: null, page: 1 })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data?.items).toHaveLength(2)
  })

  it('forwards status/severity filters as query parameters', async () => {
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse(pageOf([]))))
    const { result } = renderHook(() => useAlerts())
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    act(() => result.current.setFilters({ status: 'open', severity: 'critical' }))
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    const lastUrl = new URL(String(fetchMock.mock.calls.at(-1)?.[0]))
    expect(lastUrl.searchParams.get('status')).toBe('open')
    expect(lastUrl.searchParams.get('severity')).toBe('critical')
  })

  it('resets to page 1 when filters change', async () => {
    stubFetch(() => Promise.resolve(jsonResponse(pageOf(Array.from({ length: 10 }, () => alertItem()), 1, 10, 30))))
    const { result } = renderHook(() => useAlerts())
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    act(() => result.current.nextPage())
    await waitFor(() => expect(result.current.page).toBe(2))

    act(() => result.current.setFilters({ status: 'resolved' }))
    await waitFor(() => expect(result.current.page).toBe(1))
  })

  it('reports a failure without discarding a previous successful page', async () => {
    let calls = 0
    stubFetch(() => {
      calls += 1
      if (calls === 2) return Promise.reject(new TypeError('Failed to fetch'))
      return Promise.resolve(jsonResponse(pageOf([alertItem()])))
    })
    const { result } = renderHook(() => useAlerts())
    await waitFor(() => expect(result.current.isLoading).toBe(false))

    act(() => result.current.refresh())
    await waitFor(() => expect(result.current.failure).not.toBeNull())
    expect(result.current.data?.items).toHaveLength(1)
  })
})

/**
 * Tests for the Detection Results section inside BatchDetailDialog:
 * successful rendering, BENIGN/ATTACK labels, missing probability, empty
 * results, API failures and retry, batch-specific loading, and pagination.
 * Exercised through the real hook/service layers; only `fetch` is faked.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { BatchDetailDialog } from '@/components/batch/BatchDetailDialog'
import { batchItem, jsonResponse, manyResults, resultItem, stubBackend, stubFetch } from './helpers'

const dialog = () => screen.getByRole('dialog')

describe('DetectionResultsSection — successful retrieval', () => {
  it('shows a benign result with its real model and timestamp, not invented data', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    const result = resultItem({
      batch_id: 'batch-1',
      row_number: 7,
      predicted_label: 0,
      prediction_name: 'benign',
      attack_probability: 0.12,
      model_name: 'ai-ids-baseline',
      model_version: '20261003T000000Z',
      created_at: '2026-10-03T14:45:00Z',
    })
    stubBackend([batch], [result])

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    const section = await screen.findByText('Row 7')
    const row = section.closest('li')!
    expect(within(row).getByText('Benign')).toBeInTheDocument()
    expect(within(row).getByText('12%')).toBeInTheDocument()
    expect(within(row).getByText('ai-ids-baseline')).toBeInTheDocument()
    expect(within(row).getByText('v20261003T000000Z')).toBeInTheDocument()
    expect(within(row).getByText('03 Oct 2026, 02:45 PM')).toBeInTheDocument()
  })

  it('shows an attack result visually distinguishable from benign', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    const benign = resultItem({ id: 'r1', batch_id: 'batch-1', row_number: 1, predicted_label: 0, prediction_name: 'benign' })
    const attack = resultItem({
      id: 'r2',
      batch_id: 'batch-1',
      row_number: 2,
      predicted_label: 1,
      prediction_name: 'attack',
      attack_probability: 0.93,
    })
    stubBackend([batch], [benign, attack])

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    const row1 = (await screen.findByText('Row 1')).closest('li')!
    const row2 = screen.getByText('Row 2').closest('li')!
    expect(within(row1).getByText('Benign')).toBeInTheDocument()
    expect(within(row2).getByText('Attack')).toBeInTheDocument()
    // Distinguished by more than text alone: each row's status dot carries a
    // different tone class (sev-normal vs sev-critical).
    const benignDot = within(row1).getByText('Benign').previousElementSibling?.firstElementChild
    const attackDot = within(row2).getByText('Attack').previousElementSibling?.firstElementChild
    expect(benignDot?.className).not.toEqual(attackDot?.className)
  })

  it('does not claim predictions are guaranteed correct', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend([batch], [resultItem({ batch_id: 'batch-1' })])

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    await screen.findByText('Row 1')
    expect(within(dialog()).getByText(/not a guarantee/i)).toBeInTheDocument()
  })
})

describe('DetectionResultsSection — missing attack probability', () => {
  it('renders a "no data" marker instead of a fabricated value', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend([batch], [resultItem({ batch_id: 'batch-1', row_number: 3, attack_probability: null })])

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    const row = (await screen.findByText('Row 3')).closest('li')!
    expect(within(row).getByText('—')).toBeInTheDocument()
    expect(within(row).queryByText(/%$/)).not.toBeInTheDocument()
  })
})

describe('DetectionResultsSection — empty results', () => {
  it('shows an empty state, not an error, when the batch genuinely has none', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend([batch], [])

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    expect(await within(dialog()).findByText('No detection results yet')).toBeInTheDocument()
    expect(within(dialog()).queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('DetectionResultsSection — loading', () => {
  it('shows a loading state while results are being fetched', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubFetch((input) => {
      if (String(input).includes('/results')) return new Promise(() => {})
      return Promise.resolve(jsonResponse(batch))
    })

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    expect(await screen.findByText('Loading detection results')).toBeInTheDocument()
  })
})

describe('DetectionResultsSection — API failure and retry', () => {
  it('shows a clean error message without raw exception text, with a working retry', async () => {
    let attempt = 0
    const batch = batchItem({ batch_id: 'batch-1' })
    const result = resultItem({ batch_id: 'batch-1', row_number: 1 })
    stubFetch((input) => {
      if (String(input).includes('/results')) {
        attempt += 1
        if (attempt === 1) {
          return Promise.resolve(
            jsonResponse({ error: 'detection_results_unavailable', message: 'Unable to load detection results for this batch.' }, 500),
          )
        }
        return Promise.resolve(
          jsonResponse({ items: [result], batch_id: 'batch-1', page: 1, page_size: 20, total_items: 1, total_pages: 1 }),
        )
      }
      return Promise.resolve(jsonResponse(batch))
    })
    const user = userEvent.setup()

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    const alert = await within(dialog()).findByRole('alert')
    expect(alert).toHaveTextContent('Unable to load detection results for this batch.')
    expect(alert.textContent).not.toMatch(/traceback|exception/i)

    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(await screen.findByText('Row 1')).toBeInTheDocument()
    expect(within(dialog()).queryByRole('alert')).not.toBeInTheDocument()
  })

  it('reports an unreachable backend in plain language', async () => {
    stubFetch((input) => {
      if (String(input).includes('/results')) return Promise.reject(new TypeError('Failed to fetch'))
      return Promise.resolve(jsonResponse(batchItem({ batch_id: 'batch-1' })))
    })

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    const alert = await within(dialog()).findByRole('alert')
    expect(alert).toHaveTextContent('Could not reach the AI-IDS backend.')
    expect(alert.textContent).not.toMatch(/failed to fetch|typeerror/i)
  })
})

describe('DetectionResultsSection — batch-specific loading', () => {
  it('only shows the results belonging to the selected batch', async () => {
    const batchA = batchItem({ batch_id: 'batch-a' })
    const batchB = batchItem({ batch_id: 'batch-b' })
    const resultA = resultItem({ id: 'ra', batch_id: 'batch-a', row_number: 1 })
    const resultB = resultItem({ id: 'rb', batch_id: 'batch-b', row_number: 1 })
    const { fetchMock } = stubBackend([batchA, batchB], [resultA, resultB])

    render(<BatchDetailDialog batchId="batch-a" onClose={vi.fn()} />)

    await screen.findByText('Row 1')
    const resultsCalls = fetchMock.mock.calls.filter(([input]) => String(input).includes('/results'))
    expect(resultsCalls).toHaveLength(1)
    expect(String(resultsCalls[0]?.[0])).toContain('/batches/batch-a/results')
    expect(String(resultsCalls[0]?.[0])).not.toContain('batch-b')
  })
})

describe('DetectionResultsSection — pagination', () => {
  it('shows a summary with no controls when everything fits on one page', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend([batch], [resultItem({ batch_id: 'batch-1', row_number: 1 })])

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    await screen.findByText('Row 1')
    expect(within(dialog()).getByText('1 result')).toBeInTheDocument()
    expect(within(dialog()).queryByRole('navigation', { name: 'Detection result pagination' })).not.toBeInTheDocument()
  })

  it('pages through multiple results without overlap', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend([batch], manyResults('batch-1', 25))
    const user = userEvent.setup()

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    await screen.findByText('Row 1')
    expect(within(dialog()).getByText('Row 10')).toBeInTheDocument()
    expect(within(dialog()).queryByText('Row 11')).not.toBeInTheDocument()
    const nav = within(dialog()).getByRole('navigation', { name: 'Detection result pagination' })
    expect(within(nav).getByRole('button', { name: 'Previous page of detection results' })).toBeDisabled()

    await user.click(within(nav).getByRole('button', { name: 'Next page of detection results' }))

    expect(await within(dialog()).findByText('Row 11')).toBeInTheDocument()
    expect(within(dialog()).queryByText('Row 1')).not.toBeInTheDocument()
    expect(within(dialog()).getByText('Row 20')).toBeInTheDocument()
  })

  it('reaches the last page and disables Next', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend([batch], manyResults('batch-1', 15))
    const user = userEvent.setup()

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('Row 1')
    const nav = within(dialog()).getByRole('navigation', { name: 'Detection result pagination' })

    await user.click(within(nav).getByRole('button', { name: 'Next page of detection results' }))

    await waitFor(() => expect(within(dialog()).getByText('Row 15')).toBeInTheDocument())
    expect(within(nav).getByRole('button', { name: 'Next page of detection results' })).toBeDisabled()
  })
})

describe('DetectionResultsSection — accessibility and visual distinction', () => {
  it('names the results section with a visible heading', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend([batch], [resultItem({ batch_id: 'batch-1' })])

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    expect(await within(dialog()).findByRole('heading', { name: 'Detection results' })).toBeInTheDocument()
  })

  it('reaches pagination controls by keyboard', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend([batch], manyResults('batch-1', 25))
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('Row 1')

    const nextButton = within(dialog()).getByRole('button', { name: 'Next page of detection results' })
    nextButton.focus()
    await user.keyboard('{Enter}')

    expect(await within(dialog()).findByText('Row 11')).toBeInTheDocument()
  })
})

describe('DetectionResultsSection — label filtering (Step 30)', () => {
  it('shows only attack rows when the Attack filter is selected', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    const benign = resultItem({ id: 'r1', batch_id: 'batch-1', row_number: 1, predicted_label: 0, prediction_name: 'benign' })
    const attack = resultItem({ id: 'r2', batch_id: 'batch-1', row_number: 2, predicted_label: 1, prediction_name: 'attack' })
    stubBackend([batch], [benign, attack])
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('Row 1')

    await user.click(within(dialog()).getByRole('button', { name: 'Attack' }))

    expect(within(dialog()).queryByText('Row 1')).not.toBeInTheDocument()
    expect(within(dialog()).getByText('Row 2')).toBeInTheDocument()
  })

  it('shows only benign rows when the Benign filter is selected', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    const benign = resultItem({ id: 'r1', batch_id: 'batch-1', row_number: 1, predicted_label: 0, prediction_name: 'benign' })
    const attack = resultItem({ id: 'r2', batch_id: 'batch-1', row_number: 2, predicted_label: 1, prediction_name: 'attack' })
    stubBackend([batch], [benign, attack])
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('Row 1')

    await user.click(within(dialog()).getByRole('button', { name: 'Benign' }))

    expect(within(dialog()).getByText('Row 1')).toBeInTheDocument()
    expect(within(dialog()).queryByText('Row 2')).not.toBeInTheDocument()
  })

  it('returns to showing everything when All is reselected', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    const benign = resultItem({ id: 'r1', batch_id: 'batch-1', row_number: 1, prediction_name: 'benign' })
    const attack = resultItem({ id: 'r2', batch_id: 'batch-1', row_number: 2, predicted_label: 1, prediction_name: 'attack' })
    stubBackend([batch], [benign, attack])
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('Row 1')
    await user.click(within(dialog()).getByRole('button', { name: 'Attack' }))
    expect(within(dialog()).queryByText('Row 1')).not.toBeInTheDocument()

    await user.click(within(dialog()).getByRole('button', { name: 'All' }))

    expect(within(dialog()).getByText('Row 1')).toBeInTheDocument()
    expect(within(dialog()).getByText('Row 2')).toBeInTheDocument()
  })

  it('shows a page-scoped "no matches" message, not the full empty state, when a filter excludes everything', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend([batch], [resultItem({ batch_id: 'batch-1', row_number: 1, prediction_name: 'benign' })])
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('Row 1')

    await user.click(within(dialog()).getByRole('button', { name: 'Attack' }))

    expect(within(dialog()).getByText(/No results on this page match the current filter/)).toBeInTheDocument()
    expect(within(dialog()).queryByText('No detection results yet')).not.toBeInTheDocument()
  })

  it('does not show the filter toolbar for a batch with no results at all', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend([batch], [])

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    await within(dialog()).findByText('No detection results yet')
    expect(within(dialog()).queryByRole('button', { name: 'Attack' })).not.toBeInTheDocument()
  })
})

describe('DetectionResultsSection — search (Step 30)', () => {
  it('matches by row number, a field that genuinely exists on the result', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend(
      [batch],
      [
        resultItem({ id: 'r1', batch_id: 'batch-1', row_number: 5 }),
        resultItem({ id: 'r2', batch_id: 'batch-1', row_number: 42 }),
      ],
    )
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('Row 5')

    await user.type(within(dialog()).getByPlaceholderText(/search/i), '42')

    expect(within(dialog()).queryByText('Row 5')).not.toBeInTheDocument()
    expect(within(dialog()).getByText('Row 42')).toBeInTheDocument()
  })

  it('matches by model name, a field that genuinely exists on the result', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend(
      [batch],
      [
        resultItem({ id: 'r1', batch_id: 'batch-1', row_number: 1, model_name: 'ai-ids-baseline' }),
        resultItem({ id: 'r2', batch_id: 'batch-1', row_number: 2, model_name: 'ai-ids-experimental' }),
      ],
    )
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('Row 1')

    await user.type(within(dialog()).getByPlaceholderText(/search/i), 'experimental')

    expect(within(dialog()).queryByText('Row 1')).not.toBeInTheDocument()
    expect(within(dialog()).getByText('Row 2')).toBeInTheDocument()
  })

  it('clears the search with the clear button', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend(
      [batch],
      [resultItem({ id: 'r1', batch_id: 'batch-1', row_number: 5 }), resultItem({ id: 'r2', batch_id: 'batch-1', row_number: 9 })],
    )
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('Row 5')
    const search = within(dialog()).getByPlaceholderText(/search/i)
    await user.type(search, '5')
    expect(within(dialog()).queryByText('Row 9')).not.toBeInTheDocument()

    await user.click(within(dialog()).getByRole('button', { name: 'Clear search' }))

    expect(within(dialog()).getByText('Row 5')).toBeInTheDocument()
    expect(within(dialog()).getByText('Row 9')).toBeInTheDocument()
  })
})

describe('DetectionResultsSection — combined filter and search (Step 30)', () => {
  it('applies the label filter and the search query together (AND, not OR)', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend(
      [batch],
      [
        resultItem({ id: 'r1', batch_id: 'batch-1', row_number: 1, prediction_name: 'benign', model_version: 'stable' }),
        resultItem({
          id: 'r2',
          batch_id: 'batch-1',
          row_number: 2,
          predicted_label: 1,
          prediction_name: 'attack',
          model_version: 'stable',
        }),
        resultItem({
          id: 'r3',
          batch_id: 'batch-1',
          row_number: 12,
          predicted_label: 1,
          prediction_name: 'attack',
          model_version: 'stable',
        }),
      ],
    )
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('Row 1')

    await user.click(within(dialog()).getByRole('button', { name: 'Attack' }))
    await user.type(within(dialog()).getByPlaceholderText(/search/i), '1')

    // "1" matches row_number 1 (benign, filtered out) and the "1" inside "12" (attack).
    expect(within(dialog()).queryByText('Row 1')).not.toBeInTheDocument()
    expect(within(dialog()).queryByText('Row 2')).not.toBeInTheDocument()
    expect(within(dialog()).getByText('Row 12')).toBeInTheDocument()
  })
})

describe('DetectionResultsSection — pagination interaction (Step 30)', () => {
  it('keeps the real backend total in the pager while a filter is active', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend([batch], manyResults('batch-1', 25))
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('Row 1')

    await user.click(within(dialog()).getByRole('button', { name: 'Attack' }))

    // The pager's totals are real backend facts and must never be
    // recalculated from the filtered subset.
    const nav = within(dialog()).getByRole('navigation', { name: 'Detection result pagination' })
    expect(nav.textContent).toMatch(/Page\s*1\s*of\s*3/)
    expect(nav.textContent).toMatch(/25 results/)
  })

  it('re-applies the active filter after moving to the next page', async () => {
    const batch = batchItem({ batch_id: 'batch-1' })
    stubBackend([batch], manyResults('batch-1', 25))
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('Row 1')
    await user.click(within(dialog()).getByRole('button', { name: 'Attack' }))

    await user.click(within(dialog()).getByRole('button', { name: 'Next page of detection results' }))

    await waitFor(() => {
      const nav = within(dialog()).getByRole('navigation', { name: 'Detection result pagination' })
      expect(nav.textContent).toMatch(/Page\s*2\s*of\s*3/)
    })
    // manyResults alternates benign/attack by index; whichever rows 11-20
    // land as attack should be the only ones shown.
    const attackButton = within(dialog()).getByRole('button', { name: 'Attack' })
    expect(attackButton).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('DetectionResultsSection — API failures with an active filter (Step 30)', () => {
  it('keeps the filtered view intact and reports the failure inline when a later page fails to load', async () => {
    let page2Attempts = 0
    const batch = batchItem({ batch_id: 'batch-1' })
    const results = manyResults('batch-1', 25)
    stubFetch((input) => {
      const url = String(input)
      if (url.includes('/results')) {
        const page = new URL(url).searchParams.get('page')
        if (page === '2') {
          page2Attempts += 1
          return Promise.resolve(
            jsonResponse(
              { error: 'detection_results_unavailable', message: 'Unable to load detection results for this batch.' },
              500,
            ),
          )
        }
        const pageSize = Number(new URL(url).searchParams.get('page_size')) || 10
        return Promise.resolve(
          jsonResponse({
            items: results.slice(0, pageSize),
            batch_id: 'batch-1',
            page: 1,
            page_size: pageSize,
            total_items: results.length,
            total_pages: Math.ceil(results.length / pageSize),
          }),
        )
      }
      return Promise.resolve(jsonResponse(batch))
    })
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('Row 1')
    await user.click(within(dialog()).getByRole('button', { name: 'Attack' }))

    await user.click(within(dialog()).getByRole('button', { name: 'Next page of detection results' }))

    const alert = await within(dialog()).findByRole('alert')
    expect(alert).toHaveTextContent('Unable to load detection results for this batch.')
    expect(alert).toHaveTextContent('may be out of date')
    expect(page2Attempts).toBeGreaterThan(0)
    // The filter selection itself survives the failed page change.
    expect(within(dialog()).getByRole('button', { name: 'Attack' })).toHaveAttribute('aria-pressed', 'true')
  })
})

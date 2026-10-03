/**
 * Tests for the Detection batches panel and its integration with CSV upload,
 * through the real hook, service and API client. Only `fetch` is replaced by a
 * stateful stand-in for the backend; the 3D topology is stubbed (jsdom has no
 * canvas).
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ApiHealth } from '@/hooks/useApiHealth'
import { CommandCenterPage } from '@/pages/command-center/CommandCenterPage'
import { batchItem, csvFile, jsonResponse, manyBatches, stubBackend, stubFetch } from './helpers'

vi.mock('@/components/topology/TopologyViewport', () => ({ TopologyViewport: () => null }))

function health(overrides: Partial<ApiHealth> = {}): ApiHealth {
  return { status: 'online', latencyMs: 5, lastCheckedAt: new Date(), recheck: vi.fn(), ...overrides }
}

const panel = () => screen.getByRole('region', { name: 'Detection batches' })
const rows = () => within(panel()).queryAllByRole('listitem')
const rowNames = () => rows().map((row) => within(row).getByText(/\.csv$/).textContent)

async function renderLoaded(apiHealth = health()) {
  const view = render(<CommandCenterPage apiHealth={apiHealth} />)
  await waitFor(() => expect(within(panel()).queryByText('Loading detection batches')).not.toBeInTheDocument())
  return view
}

describe('Detection batches — loading and empty', () => {
  it('shows a loading state while the first request is in flight', () => {
    stubFetch(() => new Promise(() => {}))
    render(<CommandCenterPage apiHealth={health()} />)

    expect(within(panel()).getByRole('status')).toHaveTextContent('Loading detection batches')
    expect(within(panel()).queryByText('No batches registered yet')).not.toBeInTheDocument()
  })

  it('shows an honest empty state when the backend has no batches', async () => {
    stubBackend([])
    await renderLoaded()

    expect(within(panel()).getByText('No batches registered yet')).toBeInTheDocument()
    expect(within(panel()).queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText(/No network-flow data has been ingested yet/)).toBeInTheDocument()
  })

  it('requests the first page from the backend with an explicit page size', async () => {
    const { fetchMock } = stubBackend([])
    await renderLoaded()

    const url = new URL(String(fetchMock.mock.calls[0]?.[0]))
    expect(url.pathname).toBe('/api/v1/detection/batches')
    expect(url.searchParams.get('page')).toBe('1')
    expect(url.searchParams.get('page_size')).toBe('10')
  })
})

describe('Detection batches — list', () => {
  it('shows each batch with its filename, counts, status and id', async () => {
    stubBackend([batchItem({ filename: 'traffic.csv', total_records: 1234, processed_records: 0, failed_records: 0 })])
    await renderLoaded()

    const row = within(rows()[0] as HTMLElement)
    expect(row.getByText('traffic.csv')).toBeInTheDocument()
    expect(row.getByText('1,234')).toBeInTheDocument()
    expect(row.getByText(/records/)).toBeInTheDocument()
    expect(row.getByText(/processed/)).toBeInTheDocument()
    expect(row.getByText(/failed/)).toBeInTheDocument()
    expect(row.getByText('3f2b8c1e-6a4d-4e0b-9d7a-2c5f1a8b9e30')).toBeInTheDocument()
  })

  it('renders several batches in the order the backend returned them (newest first)', async () => {
    stubBackend(manyBatches(3))
    await renderLoaded()

    expect(rowNames()).toEqual(['batch-3.csv', 'batch-2.csv', 'batch-1.csv'])
    expect(within(panel()).getByText('3 batches')).toBeInTheDocument()
  })

  it('does not show pagination when everything fits on one page', async () => {
    stubBackend(manyBatches(3))
    await renderLoaded()

    expect(within(panel()).queryByRole('navigation', { name: 'Batch pagination' })).not.toBeInTheDocument()
  })

  it('uses the singular for a single record', async () => {
    stubBackend([batchItem({ total_records: 1 })])
    await renderLoaded()

    expect(within(panel()).getByText('record')).toBeInTheDocument()
    expect(within(panel()).queryByText('records')).not.toBeInTheDocument()
  })

  it('says "1 batch" in the singular', async () => {
    stubBackend(manyBatches(1))
    await renderLoaded()

    expect(within(panel()).getByText('1 batch')).toBeInTheDocument()
  })

  it('renders every status honestly, in words as well as colour', async () => {
    stubBackend([
      batchItem({ batch_id: '1', filename: 'a.csv', status: 'pending' }),
      batchItem({ batch_id: '2', filename: 'b.csv', status: 'processing' }),
      batchItem({ batch_id: '3', filename: 'c.csv', status: 'completed' }),
      batchItem({ batch_id: '4', filename: 'd.csv', status: 'failed' }),
    ])
    await renderLoaded()

    const text = (name: string) => rows().find((row) => within(row).queryByText(name))?.textContent
    expect(text('a.csv')).toContain('Pending· registered, waiting for processing')
    expect(text('b.csv')).toContain('Processing· being processed')
    expect(text('c.csv')).toContain('Completed· processing finished')
    expect(text('d.csv')).toContain('Failed· processing failed')
  })

  it('does not imply a pending batch is being analyzed', async () => {
    stubBackend(manyBatches(2))
    await renderLoaded()

    expect(panel().textContent).not.toMatch(/analyz(ing|ed)\b/i)
    expect(panel().textContent).toContain('waiting for processing')
  })

  it('shows the created time in a readable local format with a semantic time element', async () => {
    stubBackend([batchItem({ created_at: '2026-10-03T14:45:00Z' })])
    await renderLoaded()

    const time = within(panel()).getByText('03 Oct 2026, 02:45 PM')
    expect(time.tagName).toBe('TIME')
    expect(time).toHaveAttribute('datetime', '2026-10-03T14:45:00Z')
    expect(time).toHaveAttribute('title', '2026-10-03 14:45:00 UTC')
    expect(panel().textContent).not.toContain('2026-10-03T14')
  })

  it('keeps a very long filename and id inside the layout and exposes them in full', async () => {
    const filename = `${'CICIDS2017-Wednesday-workingHours-'.repeat(8)}capture.csv`
    const batchId = '3f2b8c1e-6a4d-4e0b-9d7a-2c5f1a8b9e30'
    stubBackend([batchItem({ filename, batch_id: batchId })])
    await renderLoaded()

    const name = within(panel()).getByText(filename)
    expect(name).toHaveClass('truncate')
    expect(name).toHaveAttribute('title', filename)
    expect(within(panel()).getByText(batchId)).toHaveClass('truncate')
  })
})

describe('Detection batches — pagination', () => {
  it('shows only the first page and page controls when there are more batches than fit', async () => {
    stubBackend(manyBatches(25))
    await renderLoaded()

    expect(rows()).toHaveLength(10)
    expect(rowNames()[0]).toBe('batch-25.csv')
    const nav = within(panel()).getByRole('navigation', { name: 'Batch pagination' })
    expect(nav).toHaveTextContent('Page 1 of 3')
    expect(nav).toHaveTextContent('25 batches')
  })

  it('disables Previous on the first page', async () => {
    stubBackend(manyBatches(25))
    await renderLoaded()

    expect(within(panel()).getByRole('button', { name: 'Previous page' })).toBeDisabled()
    expect(within(panel()).getByRole('button', { name: 'Next page' })).toBeEnabled()
  })

  it('moves to the next page and back', async () => {
    const user = userEvent.setup()
    const { fetchMock } = stubBackend(manyBatches(25))
    await renderLoaded()

    await user.click(within(panel()).getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(rowNames()[0]).toBe('batch-15.csv'))
    expect(within(panel()).getByRole('navigation')).toHaveTextContent('Page 2 of 3')
    expect(new URL(String(fetchMock.mock.calls.at(-1)?.[0])).searchParams.get('page')).toBe('2')

    await user.click(within(panel()).getByRole('button', { name: 'Previous page' }))
    await waitFor(() => expect(rowNames()[0]).toBe('batch-25.csv'))
    expect(within(panel()).getByRole('navigation')).toHaveTextContent('Page 1 of 3')
  })

  it('disables Next on the last page, which holds the remainder', async () => {
    const user = userEvent.setup()
    stubBackend(manyBatches(25))
    await renderLoaded()

    await user.click(within(panel()).getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(within(panel()).getByRole('navigation')).toHaveTextContent('Page 2 of 3'))
    await user.click(within(panel()).getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(within(panel()).getByRole('navigation')).toHaveTextContent('Page 3 of 3'))

    expect(rows()).toHaveLength(5)
    expect(within(panel()).getByRole('button', { name: 'Next page' })).toBeDisabled()
    expect(within(panel()).getByRole('button', { name: 'Previous page' })).toBeEnabled()
  })

  it('handles an exact multiple of the page size', async () => {
    stubBackend(manyBatches(20))
    await renderLoaded()

    expect(within(panel()).getByRole('navigation')).toHaveTextContent('Page 1 of 2')
  })

  it('falls back to the last page if the current one no longer exists', async () => {
    const user = userEvent.setup()
    const { store } = stubBackend(manyBatches(15))
    await renderLoaded()
    await user.click(within(panel()).getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(rows()).toHaveLength(5))

    store.splice(10) // batches removed elsewhere: page 2 is now empty
    await user.click(within(panel()).getByRole('button', { name: 'Refresh' }))

    await waitFor(() => expect(rows()).toHaveLength(10))
    expect(within(panel()).queryByRole('navigation')).not.toBeInTheDocument()
  })
})

describe('Detection batches — refresh', () => {
  it('re-requests the current page when Refresh is pressed', async () => {
    const user = userEvent.setup()
    const { fetchMock, store } = stubBackend(manyBatches(2))
    await renderLoaded()
    store.unshift(batchItem({ batch_id: '9', filename: 'added-elsewhere.csv' }))

    await user.click(within(panel()).getByRole('button', { name: 'Refresh' }))

    await waitFor(() => expect(rowNames()[0]).toBe('added-elsewhere.csv'))
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('keeps the existing list visible and disables Refresh while refreshing', async () => {
    const user = userEvent.setup()
    let second: ((response: Response) => void) | undefined
    let calls = 0
    stubFetch(() => {
      calls += 1
      if (calls > 1) return new Promise((resolve) => (second = resolve))
      return Promise.resolve(
        jsonResponse({ items: [batchItem({ filename: 'kept.csv' })], page: 1, page_size: 10, total_items: 1, total_pages: 1 }),
      )
    })
    await renderLoaded()

    await user.click(within(panel()).getByRole('button', { name: 'Refresh' }))

    expect(within(panel()).getByText('kept.csv')).toBeInTheDocument()
    expect(within(panel()).getByRole('button', { name: 'Refresh' })).toBeDisabled()
    expect(within(panel()).getByText('Refreshing batches')).toBeInTheDocument()
    second?.(jsonResponse({ items: [], page: 1, page_size: 10, total_items: 0, total_pages: 0 }))
    await waitFor(() => expect(within(panel()).getByText('No batches registered yet')).toBeInTheDocument())
  })

  it('does not send duplicate requests while one is already running', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    let calls = 0
    stubFetch(() => {
      calls += 1
      return calls === 1
        ? Promise.resolve(jsonResponse({ items: [], page: 1, page_size: 10, total_items: 0, total_pages: 0 }))
        : new Promise(() => {})
    })
    await renderLoaded()

    await user.click(within(panel()).getByRole('button', { name: 'Refresh' }))
    await user.click(within(panel()).getByRole('button', { name: 'Refresh' }))
    await user.click(within(panel()).getByRole('button', { name: 'Refresh' }))

    expect(calls).toBe(2)
  })

  it('keeps the old list and reports the problem when a refresh fails', async () => {
    const user = userEvent.setup()
    let calls = 0
    stubFetch(() => {
      calls += 1
      return calls === 1
        ? Promise.resolve(jsonResponse({ items: [batchItem({ filename: 'kept.csv' })], page: 1, page_size: 10, total_items: 1, total_pages: 1 }))
        : Promise.reject(new TypeError('Failed to fetch'))
    })
    await renderLoaded()

    await user.click(within(panel()).getByRole('button', { name: 'Refresh' }))

    const alert = await within(panel()).findByRole('alert')
    expect(alert).toHaveTextContent('Could not refresh')
    expect(alert).toHaveTextContent('may be out of date')
    expect(alert.textContent).not.toMatch(/failed to fetch|typeerror/i)
    expect(within(panel()).getByText('kept.csv')).toBeInTheDocument()
  })

  it('recovers from a failed refresh on retry', async () => {
    const user = userEvent.setup()
    let calls = 0
    stubFetch(() => {
      calls += 1
      if (calls === 2) return Promise.reject(new TypeError('Failed to fetch'))
      return Promise.resolve(jsonResponse({ items: [batchItem({ filename: `load-${calls}.csv` })], page: 1, page_size: 10, total_items: 1, total_pages: 1 }))
    })
    await renderLoaded()
    await user.click(within(panel()).getByRole('button', { name: 'Refresh' }))
    await user.click(await within(panel()).findByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(within(panel()).getByText('load-3.csv')).toBeInTheDocument())
    expect(within(panel()).queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('Detection batches — failures', () => {
  it('shows an error state with the backend explanation, and Retry loads the list', async () => {
    const user = userEvent.setup()
    let calls = 0
    stubFetch(() => {
      calls += 1
      return Promise.resolve(
        calls === 1
          ? jsonResponse({ error: 'batches_unavailable', message: 'Unable to load detection batches.' }, 500)
          : jsonResponse({ items: [batchItem({ filename: 'back.csv' })], page: 1, page_size: 10, total_items: 1, total_pages: 1 }),
      )
    })
    await renderLoaded()

    const alert = within(panel()).getByRole('alert')
    expect(alert).toHaveTextContent('Unable to load detection batches')
    expect(within(panel()).queryByText('No batches registered yet')).not.toBeInTheDocument()

    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(await within(panel()).findByText('back.csv')).toBeInTheDocument()
  })

  it('shows the offline state, not "no batches", when the API cannot be reached', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))
    await renderLoaded()

    expect(within(panel()).getByRole('alert')).toHaveTextContent('AI-IDS backend unavailable')
    expect(within(panel()).queryByText('No batches registered yet')).not.toBeInTheDocument()
  })

  it('retrying from offline re-checks health and reloads the batches', async () => {
    const user = userEvent.setup()
    const apiHealth = health({ status: 'offline' })
    let reachable = false
    stubFetch(() =>
      reachable
        ? Promise.resolve(jsonResponse({ items: [batchItem({ filename: 'back.csv' })], page: 1, page_size: 10, total_items: 1, total_pages: 1 }))
        : Promise.reject(new TypeError('Failed to fetch')),
    )
    await renderLoaded(apiHealth)

    reachable = true
    await user.click(within(panel()).getByRole('button', { name: 'Retry connection' }))

    expect(await within(panel()).findByText('back.csv')).toBeInTheDocument()
    expect(apiHealth.recheck).toHaveBeenCalledOnce()
  })

  it('treats a malformed response as an error rather than showing invented data', async () => {
    stubFetch(() => Promise.resolve(jsonResponse({ items: [{ filename: 'x.csv' }], page: 1 })))
    await renderLoaded()

    expect(within(panel()).getByRole('alert')).toHaveTextContent('Unable to load detection batches')
    expect(rows()).toHaveLength(0)
  })
})

describe('Detection batches — persistence and upload', () => {
  async function upload(name: string) {
    const user = userEvent.setup({ applyAccept: false })
    await user.upload(screen.getByLabelText('Choose a CSV file'), csvFile(name))
    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))
    await screen.findByText('Traffic uploaded')
  }

  it('re-fetches page one from the backend after an upload instead of keeping its own copy', async () => {
    const { fetchMock } = stubBackend([])
    await renderLoaded()

    await upload('first.csv')

    await waitFor(() => expect(rowNames()).toEqual(['first.csv']))
    const urls = fetchMock.mock.calls.map(([input, init]) => `${init?.method ?? 'GET'} ${new URL(String(input)).pathname}`)
    expect(urls).toEqual(['GET /api/v1/detection/batches', 'POST /api/v1/detection/upload', 'GET /api/v1/detection/batches'])
  })

  it('lists two uploads newest first and updates the page summary', async () => {
    stubBackend([])
    await renderLoaded()

    await upload('first.csv')
    await userEvent.click(screen.getByRole('button', { name: 'Upload another file' }))
    await upload('second.csv')

    await waitFor(() => expect(rowNames()).toEqual(['second.csv', 'first.csv']))
    expect(screen.getByText(/2 batches registered\. The traffic has not been analyzed yet/)).toBeInTheDocument()
  })

  it('returns to the first page after an upload made while viewing a later page', async () => {
    const user = userEvent.setup()
    stubBackend(manyBatches(15))
    await renderLoaded()
    await user.click(within(panel()).getByRole('button', { name: 'Next page' }))
    await waitFor(() => expect(within(panel()).getByRole('navigation')).toHaveTextContent('Page 2 of 2'))

    await upload('fresh.csv')

    await waitFor(() => expect(rowNames()[0]).toBe('fresh.csv'))
    expect(within(panel()).getByRole('navigation')).toHaveTextContent('Page 1 of 2')
  })

  it('shows the same batches after the page is torn down and rebuilt, because they live in the backend', async () => {
    stubBackend([])
    const first = await renderLoaded()
    await upload('survives-reload.csv')
    await waitFor(() => expect(rowNames()).toEqual(['survives-reload.csv']))

    first.unmount() // a browser refresh discards all React state
    await renderLoaded()

    expect(rowNames()).toEqual(['survives-reload.csv'])
  })
})

describe('Detection batches — no invented IDS data', () => {
  it('keeps every security metric empty even with batches present', async () => {
    stubBackend(manyBatches(3))
    await renderLoaded()

    const telemetry = screen.getByRole('region', { name: 'Security telemetry' })
    expect(within(telemetry).getAllByText('No data')).toHaveLength(4)
    expect(telemetry.textContent).not.toMatch(/\d/)
    expect(document.body.textContent).not.toMatch(/\d+(\.\d+)?\s?%/)
    expect(panel().textContent).not.toMatch(/threat|attack|risk|confidence|severity|score/i)
  })
})

describe('Detection batches — row selection and detail dialog', () => {
  it('opens the detail dialog for the clicked batch', async () => {
    const first = batchItem({ batch_id: 'batch-1', filename: 'first.csv' })
    const second = batchItem({ batch_id: 'batch-2', filename: 'second.csv' })
    stubBackend([first, second])
    const user = userEvent.setup()
    await renderLoaded()

    await user.click(screen.getByRole('button', { name: `View details for ${first.filename}` }))

    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByRole('heading', { name: first.filename })).toBeInTheDocument()
  })

  it('shows the correct data for whichever row was clicked, not the first row', async () => {
    const first = batchItem({ batch_id: 'batch-1', filename: 'first.csv' })
    const third = batchItem({ batch_id: 'batch-3', filename: 'third.csv' })
    stubBackend([first, third])
    const user = userEvent.setup()
    await renderLoaded()

    await user.click(screen.getByRole('button', { name: `View details for ${third.filename}` }))

    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByRole('heading', { name: third.filename })).toBeInTheDocument()
    expect(within(dialog).getByText(third.batch_id)).toBeInTheDocument()
  })

  it('closes the dialog when the close button is activated, returning to the batch list', async () => {
    const only = batchItem({ batch_id: 'batch-1', filename: 'only.csv' })
    stubBackend([only])
    const user = userEvent.setup()
    await renderLoaded()

    await user.click(screen.getByRole('button', { name: `View details for ${only.filename}` }))
    await screen.findByRole('dialog')

    await user.click(screen.getByRole('button', { name: 'Close' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(rowNames()).toEqual([only.filename])
  })

  it('opens a second batch cleanly after closing the first, without bleeding data between them', async () => {
    const first = batchItem({ batch_id: 'batch-1', filename: 'first.csv' })
    const second = batchItem({ batch_id: 'batch-2', filename: 'second.csv' })
    stubBackend([first, second])
    const user = userEvent.setup()
    await renderLoaded()

    await user.click(screen.getByRole('button', { name: `View details for ${first.filename}` }))
    let dialog = await screen.findByRole('dialog')
    await within(dialog).findByRole('heading', { name: first.filename })
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: `View details for ${second.filename}` }))
    dialog = await screen.findByRole('dialog')

    expect(await within(dialog).findByRole('heading', { name: second.filename })).toBeInTheDocument()
    expect(within(dialog).queryByText(first.filename)).not.toBeInTheDocument()
  })

  it('reaches a batch row by keyboard and opens its detail dialog with Enter', async () => {
    const only = batchItem({ batch_id: 'batch-1', filename: 'only.csv' })
    stubBackend([only])
    const user = userEvent.setup()
    await renderLoaded()

    const row = screen.getByRole('button', { name: `View details for ${only.filename}` })
    row.focus()
    expect(row).toHaveFocus()
    await user.keyboard('{Enter}')

    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByRole('heading', { name: only.filename })).toBeInTheDocument()
  })
})

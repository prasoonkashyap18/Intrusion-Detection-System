/**
 * Tests for the batch detail dialog: loading, success content, not-found vs.
 * generic failure, retry, close behaviour, and the copy-id affordance.
 * Exercised through the real hook and service layers; only `fetch` is faked.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { BatchDetailDialog } from '@/components/batch/BatchDetailDialog'
import { batchItem, jsonResponse, stubBackend, stubFetch } from './helpers'

const dialog = () => screen.getByRole('dialog')

describe('BatchDetailDialog — loading', () => {
  it('opens as a modal and shows a loading state while the batch is fetched', () => {
    stubFetch(() => new Promise(() => {}))

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    expect(dialog()).toBeInTheDocument()
    expect(within(dialog()).getByRole('status')).toHaveTextContent('Loading batch detail')
    expect(screen.getByRole('heading', { name: 'Batch detail' })).toBeInTheDocument()
  })
})

describe('BatchDetailDialog — success', () => {
  it('shows the batch as the backend reported it, with no invented data', async () => {
    const batch = batchItem({
      batch_id: '3f2b8c1e-6a4d-4e0b-9d7a-2c5f1a8b9e30',
      filename: 'traffic.csv',
      status: 'pending',
      total_records: 1234,
      processed_records: 0,
      failed_records: 0,
      created_at: '2026-10-03T14:45:00Z',
      completed_at: null,
    })
    stubBackend([batch])

    render(<BatchDetailDialog batchId={batch.batch_id} onClose={vi.fn()} />)

    expect(await screen.findByRole('heading', { name: 'traffic.csv' })).toBeInTheDocument()
    const body = dialog()
    expect(within(body).getByText('Pending')).toBeInTheDocument()
    expect(within(body).getByText(/registered, waiting for processing/)).toBeInTheDocument()
    expect(within(body).getByText('1,234')).toBeInTheDocument()
    expect(within(body).getAllByText('0')).toHaveLength(2) // processed, failed
    expect(within(body).getByText('03 Oct 2026, 02:45 PM')).toBeInTheDocument()
    expect(within(body).getByText('Not yet completed')).toBeInTheDocument()
    expect(within(body).getByText(batch.batch_id)).toBeInTheDocument()
    expect(within(body).getByText('No detection results yet')).toBeInTheDocument()
    expect(body.textContent).not.toMatch(/threat|attack|risk|confidence|severity|score|%/i)
  })

  it('shows the completion time for a completed batch instead of "Not yet completed"', async () => {
    const batch = batchItem({ status: 'completed', completed_at: '2026-10-03T15:00:00Z' })
    stubBackend([batch])

    render(<BatchDetailDialog batchId={batch.batch_id} onClose={vi.fn()} />)

    // "Completed" appears twice once loaded: the status label and the field name.
    await waitFor(() => expect(screen.getAllByText('Completed')).toHaveLength(2))
    expect(screen.getByText('03 Oct 2026, 03:00 PM')).toBeInTheDocument()
    expect(screen.queryByText('Not yet completed')).not.toBeInTheDocument()
  })

  it('exposes the exact UTC instant as a tooltip on the local-time display', async () => {
    const batch = batchItem({ created_at: '2026-10-03T14:45:00Z' })
    stubBackend([batch])

    render(<BatchDetailDialog batchId={batch.batch_id} onClose={vi.fn()} />)

    const time = await screen.findByText('03 Oct 2026, 02:45 PM')
    expect(time.tagName).toBe('TIME')
    expect(time).toHaveAttribute('datetime', '2026-10-03T14:45:00Z')
    expect(time).toHaveAttribute('title', '2026-10-03 14:45:00 UTC')
  })

  it('copies the batch id to the clipboard and shows brief confirmation', async () => {
    // userEvent.setup() installs its own functional navigator.clipboard stub
    // (overwriting any manual mock), so the real Clipboard API is exercised
    // and verified via readText() rather than a spy.
    const batch = batchItem({ batch_id: 'copy-me-id' })
    stubBackend([batch])
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId={batch.batch_id} onClose={vi.fn()} />)
    await screen.findByText('copy-me-id')
    const copyButton = screen.getByRole('button', { name: 'Copy batch ID' })
    expect(copyButton.querySelector('.lucide-copy')).toBeInTheDocument()

    await user.click(copyButton)

    await expect(navigator.clipboard.readText()).resolves.toBe('copy-me-id')
    await waitFor(() => expect(copyButton.querySelector('.lucide-check')).toBeInTheDocument())
  })
})

describe('BatchDetailDialog — not found', () => {
  it('shows a clean not-found message without offering a pointless retry', async () => {
    stubBackend([])

    render(<BatchDetailDialog batchId="missing-id" onClose={vi.fn()} />)

    const alert = await within(dialog()).findByRole('alert')
    expect(alert).toHaveTextContent('Batch not found')
    expect(alert).toHaveTextContent('No batch was found with that ID.')
    expect(within(alert).queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })
})

describe('BatchDetailDialog — failure and retry', () => {
  it('shows a generic failure with Retry when the backend errors', async () => {
    stubFetch(() =>
      Promise.resolve(jsonResponse({ error: 'batch_unavailable', message: 'Unable to load the detection batch.' }, 500)),
    )

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    const alert = await within(dialog()).findByRole('alert')
    expect(alert).toHaveTextContent('Unable to load this batch')
    expect(alert).toHaveTextContent('Unable to load the detection batch.')
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('reports an unreachable backend in plain language, never raw network text', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    const alert = await within(dialog()).findByRole('alert')
    expect(alert).toHaveTextContent('Could not reach the AI-IDS backend.')
    expect(alert.textContent).not.toMatch(/failed to fetch|typeerror/i)
  })

  it('recovers after Retry once the backend succeeds', async () => {
    let attempt = 0
    const batch = batchItem({ filename: 'recovered.csv' })
    stubFetch(() => {
      attempt += 1
      return Promise.resolve(
        attempt === 1
          ? jsonResponse({ error: 'batch_unavailable', message: 'Unable to load the detection batch.' }, 500)
          : jsonResponse(batch),
      )
    })
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId={batch.batch_id} onClose={vi.fn()} />)
    await within(dialog()).findByRole('alert')

    await user.click(within(dialog()).getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('heading', { name: 'recovered.csv' })).toBeInTheDocument()
    expect(within(dialog()).queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('BatchDetailDialog — dismissal', () => {
  it('calls onClose when the close button is activated', async () => {
    stubBackend([batchItem({ batch_id: 'batch-1' })])
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={onClose} />)
    await screen.findByText('batch-1')

    await user.click(screen.getByRole('button', { name: 'Close' }))

    expect(onClose).toHaveBeenCalledOnce()
  })

  it('calls onClose when Escape is pressed', async () => {
    stubBackend([batchItem({ batch_id: 'batch-1' })])
    const onClose = vi.fn()
    render(<BatchDetailDialog batchId="batch-1" onClose={onClose} />)
    await screen.findByText('batch-1')

    dialog().dispatchEvent(new Event('close'))

    expect(onClose).toHaveBeenCalledOnce()
  })

  it('locks page scroll while open and restores it on unmount', async () => {
    stubBackend([batchItem({ batch_id: 'batch-1' })])
    const previous = document.body.style.overflow
    const { unmount } = render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('batch-1')

    expect(document.body.style.overflow).toBe('hidden')

    unmount()
    expect(document.body.style.overflow).toBe(previous)
  })
})

describe('BatchDetailDialog — starting processing', () => {
  it('shows Start Processing for a pending batch', async () => {
    stubBackend([batchItem({ batch_id: 'batch-1', status: 'pending' })])

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    expect(await screen.findByRole('button', { name: 'Start Processing' })).toBeEnabled()
  })

  it('calls the real processing endpoint and updates the displayed status', async () => {
    stubBackend([batchItem({ batch_id: 'batch-1', status: 'pending' })])
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByRole('button', { name: 'Start Processing' })

    await user.click(screen.getByRole('button', { name: 'Start Processing' }))

    await screen.findByText(/being processed/)
    expect(screen.queryByRole('button', { name: 'Start Processing' })).not.toBeInTheDocument()
  })

  it('disables the action while the request is in flight', async () => {
    const batch = batchItem({ batch_id: 'batch-1', status: 'pending' })
    let resolveProcess: (() => void) | undefined
    stubFetch((input) => {
      if (String(input).endsWith('/process')) {
        return new Promise((resolve) => {
          resolveProcess = () => resolve(jsonResponse({ batch_id: 'batch-1', status: 'processing', message: 'ok' }))
        })
      }
      return Promise.resolve(jsonResponse(batch))
    })
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    const button = await screen.findByRole('button', { name: 'Start Processing' })

    await user.click(button)

    expect(screen.getByRole('button', { name: 'Starting…' })).toBeDisabled()

    resolveProcess?.()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Starting…' })).not.toBeInTheDocument())
  })

  it('does not offer Start Processing for a completed batch', async () => {
    stubBackend([batchItem({ batch_id: 'batch-1', status: 'completed', completed_at: '2026-10-03T09:00:00Z' })])

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    await screen.findByText(/processing finished/)
    expect(screen.queryByRole('button', { name: /start processing/i })).not.toBeInTheDocument()
  })

  it('does not offer Start Processing for a failed batch', async () => {
    stubBackend([batchItem({ batch_id: 'batch-1', status: 'failed' })])

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    await screen.findByText(/processing failed/)
    expect(screen.queryByRole('button', { name: /start processing/i })).not.toBeInTheDocument()
  })

  it('shows a clean failure message when starting processing fails, without raw exception text', async () => {
    const batch = batchItem({ batch_id: 'batch-1', status: 'pending' })
    stubFetch((input) => {
      if (String(input).endsWith('/process')) {
        return Promise.resolve(jsonResponse({ error: 'invalid_batch_state', message: 'Batch is already processing.' }, 409))
      }
      return Promise.resolve(jsonResponse(batch))
    })
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: 'Start Processing' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Batch is already processing.')
    expect(alert.textContent).not.toMatch(/invalid_batch_state|exception|traceback/i)
    // The button stays usable: this is a conflict the user can see and retry, not a crash.
    expect(screen.getByRole('button', { name: 'Start Processing' })).toBeEnabled()
  })

  it('shows no fake IDS metrics once processing has started', async () => {
    stubBackend([batchItem({ batch_id: 'batch-1', status: 'pending' })])
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: 'Start Processing' }))

    await screen.findByText(/being processed/)
    expect(dialog().textContent).not.toMatch(/threat|attack|risk|confidence|severity|score|%/i)
  })
})

describe('BatchDetailDialog — accessibility', () => {
  it('names the dialog from its visible heading', async () => {
    stubBackend([batchItem({ batch_id: 'batch-1', filename: 'named.csv' })])

    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)

    await waitFor(() => expect(dialog()).toHaveAccessibleName('named.csv'))
  })

  it('reaches the close button by keyboard', async () => {
    stubBackend([batchItem({ batch_id: 'batch-1' })])
    const user = userEvent.setup()
    render(<BatchDetailDialog batchId="batch-1" onClose={vi.fn()} />)
    await screen.findByText('batch-1')

    await user.tab()

    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
  })
})

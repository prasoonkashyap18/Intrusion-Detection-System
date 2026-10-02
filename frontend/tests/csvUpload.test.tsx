/**
 * Tests for the CSV upload component, exercised through the real hook and
 * service layers. Only `fetch` is stubbed — no test contacts a backend.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { CsvUpload } from '@/components/upload/CsvUpload'
import { API_BASE_URL, MAX_UPLOAD_BYTES } from '@/services'
import { batchResponse, csvFile, jsonResponse, sentFile, stubFetch } from './helpers'

// Browsers let users bypass the file picker's `accept` filter; keep that path testable.
const setup = () => userEvent.setup({ applyAccept: false })

const input = () => screen.getByLabelText('Choose a CSV file')
const zone = () => screen.getByRole('group', { name: 'CSV upload area' })

async function selectFile(user: ReturnType<typeof setup>, file = csvFile()) {
  await user.upload(input(), file)
  return file
}

function dataTransfer(files: File[], types: string[] = ['Files']) {
  return { dataTransfer: { files, types, dropEffect: 'none' } }
}

describe('CsvUpload — idle', () => {
  it('invites the user to upload without implying analysis', () => {
    render(<CsvUpload />)

    expect(screen.getByText('Upload network traffic')).toBeInTheDocument()
    expect(screen.getByText('Drop a CSV here or choose a file.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Choose file' })).toBeEnabled()
    expect(screen.getByText(/not analyzed yet/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Upload CSV' })).not.toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/analyzing/i)
  })

  it('opens the file chooser from the keyboard-reachable button', async () => {
    const user = setup()
    render(<CsvUpload />)
    const clicked = vi.spyOn(input(), 'click')

    await user.tab()
    expect(screen.getByRole('button', { name: 'Choose file' })).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(clicked).toHaveBeenCalledOnce()
  })

  it('restricts the native picker to CSV files', () => {
    render(<CsvUpload />)

    expect(input()).toHaveAttribute('accept', '.csv,text/csv')
  })
})

describe('CsvUpload — selecting a file', () => {
  it('shows the selected filename and size and offers to upload or replace it', async () => {
    const user = setup()
    render(<CsvUpload />)

    await selectFile(user, csvFile('capture-2026.csv', { size: 2.4 * 1024 * 1024 }))

    expect(screen.getByText('capture-2026.csv')).toBeInTheDocument()
    expect(screen.getByText('2.4 MB')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload CSV' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Replace file' })).toBeEnabled()
  })

  it('lets the user replace the selected file', async () => {
    const user = setup()
    render(<CsvUpload />)
    await selectFile(user, csvFile('first.csv'))

    await selectFile(user, csvFile('second.csv'))

    expect(screen.getByText('second.csv')).toBeInTheDocument()
    expect(screen.queryByText('first.csv')).not.toBeInTheDocument()
  })

  it('rejects a file without a .csv extension before uploading', async () => {
    const user = setup()
    const fetchMock = stubFetch(() => Promise.reject(new Error('should not be called')))
    render(<CsvUpload />)

    await selectFile(user, csvFile('notes.txt'))

    expect(screen.getByRole('alert')).toHaveTextContent('Only .csv files are supported.')
    expect(screen.queryByRole('button', { name: 'Upload CSV' })).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('rejects an empty file', async () => {
    const user = setup()
    render(<CsvUpload />)

    await selectFile(user, csvFile('empty.csv', { size: 0 }))

    expect(screen.getByRole('alert')).toHaveTextContent('This file is empty.')
    expect(screen.queryByRole('button', { name: 'Upload CSV' })).not.toBeInTheDocument()
  })

  it('rejects a file over the size limit and states the limit', async () => {
    const user = setup()
    render(<CsvUpload />)

    await selectFile(user, csvFile('huge.csv', { size: MAX_UPLOAD_BYTES + 1 }))

    expect(screen.getByRole('alert')).toHaveTextContent('maximum upload size is 50 MB')
    expect(screen.queryByRole('button', { name: 'Upload CSV' })).not.toBeInTheDocument()
  })

  it('accepts a file exactly at the size limit', async () => {
    const user = setup()
    render(<CsvUpload />)

    await selectFile(user, csvFile('limit.csv', { size: MAX_UPLOAD_BYTES }))

    expect(screen.getByRole('button', { name: 'Upload CSV' })).toBeEnabled()
  })

  it('explains the refusal in text, not just with an icon or colour', async () => {
    const user = setup()
    render(<CsvUpload />)

    await selectFile(user, csvFile('notes.txt'))

    expect(screen.getByRole('alert')).toHaveTextContent(/File not accepted/)
  })
})

describe('CsvUpload — drag and drop', () => {
  it('gives subtle feedback while a file is dragged over the zone', () => {
    render(<CsvUpload />)

    fireEvent.dragEnter(zone(), dataTransfer([csvFile()]))

    expect(zone()).toHaveAttribute('data-drag-active')
    expect(screen.getByText('Release to select this file')).toBeInTheDocument()

    fireEvent.dragLeave(zone(), { relatedTarget: null })
    expect(zone()).not.toHaveAttribute('data-drag-active')
  })

  it('selects a dropped file', () => {
    render(<CsvUpload />)

    fireEvent.drop(zone(), dataTransfer([csvFile('dropped.csv')]))

    expect(screen.getByText('dropped.csv')).toBeInTheDocument()
    expect(zone()).not.toHaveAttribute('data-drag-active')
    expect(screen.getByRole('button', { name: 'Upload CSV' })).toBeEnabled()
  })

  it('validates a dropped file like a picked one', () => {
    render(<CsvUpload />)

    fireEvent.drop(zone(), dataTransfer([csvFile('image.png')]))

    expect(screen.getByRole('alert')).toHaveTextContent('Only .csv files are supported.')
  })

  it('refuses several dropped files at once', () => {
    render(<CsvUpload />)

    fireEvent.drop(zone(), dataTransfer([csvFile('a.csv'), csvFile('b.csv')]))

    expect(screen.getByRole('alert')).toHaveTextContent('Drop a single CSV file.')
    expect(screen.queryByText('a.csv')).not.toBeInTheDocument()
  })

  it('ignores drags that are not files, such as selected text', () => {
    render(<CsvUpload />)

    fireEvent.dragEnter(zone(), dataTransfer([], ['text/plain']))

    expect(zone()).not.toHaveAttribute('data-drag-active')
  })

  it('is scoped to the upload zone: drops elsewhere do nothing', () => {
    render(
      <div>
        <p>elsewhere</p>
        <CsvUpload />
      </div>,
    )

    fireEvent.drop(screen.getByText('elsewhere'), dataTransfer([csvFile('stray.csv')]))

    expect(screen.queryByText('stray.csv')).not.toBeInTheDocument()
  })

  it('is not the only way to upload: the file input and button work without dragging', async () => {
    const user = setup()
    render(<CsvUpload />)

    await selectFile(user, csvFile('picked.csv'))

    expect(screen.getByText('picked.csv')).toBeInTheDocument()
  })
})

describe('CsvUpload — uploading', () => {
  it('sends the selected file to the upload endpoint as multipart form data', async () => {
    const user = setup()
    const fetchMock = stubFetch(() => Promise.resolve(jsonResponse(batchResponse(), 201)))
    render(<CsvUpload />)
    await selectFile(user, csvFile('flows.csv'))

    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))
    await screen.findByText('Traffic uploaded')

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0] ?? []
    expect(url).toBe(`${API_BASE_URL}/api/v1/detection/upload`)
    expect(init?.method).toBe('POST')
    expect(sentFile(init).name).toBe('flows.csv')
    // The browser must set the multipart boundary itself.
    expect(init?.headers).not.toHaveProperty('Content-Type')
  })

  it('shows a truthful uploading state with the actions disabled and no invented progress', async () => {
    const user = setup()
    const fetchMock = stubFetch(() => new Promise(() => {}))
    render(<CsvUpload />)
    await selectFile(user)

    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))

    expect(screen.getByRole('status')).toHaveTextContent('Uploading…')
    expect(screen.getByRole('button', { name: 'Uploading…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Replace file' })).toBeDisabled()
    // Real byte progress is unavailable, so nothing pretends to measure it.
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/\d+\s?%/)
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('does not say "analyzing" while uploading', async () => {
    const user = setup()
    stubFetch(() => new Promise(() => {}))
    render(<CsvUpload />)
    await selectFile(user)

    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))

    expect(screen.getByRole('status')).toHaveTextContent('Nothing is analyzed yet')
    expect(document.body.textContent).not.toMatch(/analyzing/i)
  })

  it('does not submit twice when the button is activated repeatedly', async () => {
    // The disabled button also sets pointer-events: none, which user-event
    // would otherwise reject before attempting the click.
    const user = userEvent.setup({ applyAccept: false, pointerEventsCheck: 0 })
    const fetchMock = stubFetch(() => new Promise(() => {}))
    render(<CsvUpload />)
    await selectFile(user)

    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))
    await user.click(screen.getByRole('button', { name: 'Uploading…' }))

    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('ignores files dropped while an upload is in flight', async () => {
    const user = setup()
    stubFetch(() => new Promise(() => {}))
    render(<CsvUpload />)
    await selectFile(user, csvFile('first.csv'))
    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))

    fireEvent.drop(zone(), dataTransfer([csvFile('second.csv')]))

    expect(screen.getByText('first.csv')).toBeInTheDocument()
    expect(screen.queryByText('second.csv')).not.toBeInTheDocument()
  })
})

describe('CsvUpload — success', () => {
  it('shows only what the backend returned and says the traffic is not analyzed', async () => {
    const user = setup()
    const onUploaded = vi.fn()
    stubFetch(() => Promise.resolve(jsonResponse(batchResponse({ filename: 'traffic.csv', total_records: 1234 }), 201)))
    render(<CsvUpload onUploaded={onUploaded} />)
    await selectFile(user)

    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))

    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent('Traffic uploaded')
    expect(status).toHaveTextContent('Batch registered successfully.')
    expect(status).toHaveTextContent('The traffic has not been analyzed yet.')
    expect(status).toHaveTextContent('3f2b8c1e-6a4d-4e0b-9d7a-2c5f1a8b9e30')
    expect(status).toHaveTextContent('traffic.csv')
    expect(status).toHaveTextContent('1,234')
    expect(status).toHaveTextContent('pending')
    expect(status).toHaveTextContent('2026-10-03 08:15:30 UTC')
    expect(onUploaded).toHaveBeenCalledExactlyOnceWith(batchResponse())
  })

  it('does not display any detection or risk figures', async () => {
    const user = setup()
    stubFetch(() => Promise.resolve(jsonResponse(batchResponse(), 201)))
    render(<CsvUpload />)
    await selectFile(user)

    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))
    const status = await screen.findByRole('status')

    expect(status.textContent).not.toMatch(/threat|attack|risk|confidence|severity|%|score/i)
  })

  it('lets the user upload another file afterwards', async () => {
    const user = setup()
    stubFetch(() => Promise.resolve(jsonResponse(batchResponse(), 201)))
    render(<CsvUpload />)
    await selectFile(user)
    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))

    await user.click(await screen.findByRole('button', { name: 'Upload another file' }))

    expect(screen.getByText('Upload network traffic')).toBeInTheDocument()
    expect(screen.queryByText('Traffic uploaded')).not.toBeInTheDocument()
  })
})

describe('CsvUpload — failures', () => {
  it("shows the backend's own explanation when it rejects the file", async () => {
    const user = setup()
    stubFetch(() => Promise.resolve(jsonResponse({ error: 'unsupported_media_type', message: 'Only CSV files are supported.' }, 415)))
    render(<CsvUpload />)
    await selectFile(user)

    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Upload failed')
    expect(alert).toHaveTextContent('Only CSV files are supported.')
    // Resending a rejected file cannot succeed, so no Retry is offered.
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Choose a different file' })).toBeInTheDocument()
  })

  it('offers Retry for a server error and re-sends the same file', async () => {
    const user = setup()
    let attempt = 0
    const fetchMock = stubFetch(() => {
      attempt += 1
      return Promise.resolve(
        attempt === 1
          ? jsonResponse({ error: 'upload_failed', message: 'Unable to register the uploaded file.' }, 500)
          : jsonResponse(batchResponse(), 201),
      )
    })
    render(<CsvUpload />)
    await selectFile(user, csvFile('flows.csv'))
    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to register the uploaded file.')

    await user.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByText('Traffic uploaded')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const names = fetchMock.mock.calls.map(([, init]) => sentFile(init).name)
    expect(names).toEqual(['flows.csv', 'flows.csv'])
  })

  it('reports an unreachable backend in plain language, never the raw network error', async () => {
    const user = setup()
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))
    render(<CsvUpload />)
    await selectFile(user)

    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not reach the AI-IDS backend.')
    expect(alert.textContent).not.toMatch(/failed to fetch|typeerror|network_error/i)
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('lets the user back out and choose a different file after an error', async () => {
    const user = setup()
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))
    render(<CsvUpload />)
    await selectFile(user)
    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))

    await user.click(await screen.findByRole('button', { name: 'Choose a different file' }))

    expect(screen.getByRole('button', { name: 'Choose file' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('treats a malformed success response as a failure rather than showing invented data', async () => {
    const user = setup()
    stubFetch(() => Promise.resolve(jsonResponse({ batch_id: 'x' }, 201)))
    render(<CsvUpload />)
    await selectFile(user)

    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.queryByText('Traffic uploaded')).not.toBeInTheDocument()
  })
})

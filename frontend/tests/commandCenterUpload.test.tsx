/**
 * Integration test: uploading a CSV through the Command Center registers a
 * batch that appears in the batches panel — without any invented IDS data.
 * The 3D topology is replaced with a stub (jsdom has no canvas), and `fetch`
 * is the only other thing faked.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ApiHealth } from '@/hooks/useApiHealth'
import { CommandCenterPage } from '@/pages/command-center/CommandCenterPage'
import { batchResponse, csvFile, jsonResponse, stubFetch } from './helpers'

vi.mock('@/components/topology/TopologyViewport', () => ({ TopologyViewport: () => null }))

const online: ApiHealth = { status: 'online', latencyMs: 5, lastCheckedAt: new Date(), recheck: vi.fn() }
const offline: ApiHealth = { status: 'offline', latencyMs: null, lastCheckedAt: new Date(), recheck: vi.fn() }

const batchesPanel = () => screen.getByRole('region', { name: 'Detection batches' })

async function uploadCsv(file = csvFile('traffic.csv')) {
  const user = userEvent.setup({ applyAccept: false })
  await user.upload(screen.getByLabelText('Choose a CSV file'), file)
  await user.click(screen.getByRole('button', { name: 'Upload CSV' }))
  await screen.findByText('Traffic uploaded')
}

describe('Command Center — CSV upload', () => {
  it('starts with no batches and no claim that data was ingested', () => {
    render(<CommandCenterPage apiHealth={online} />)

    expect(within(batchesPanel()).getByText('No batches registered yet')).toBeInTheDocument()
    expect(screen.getByText(/No network-flow data has been ingested yet/)).toBeInTheDocument()
  })

  it('shows the newly registered batch in the Detection batches panel without a reload', async () => {
    stubFetch(() => Promise.resolve(jsonResponse(batchResponse({ filename: 'traffic.csv', total_records: 1234 }), 201)))
    render(<CommandCenterPage apiHealth={online} />)

    await uploadCsv()

    const panel = within(batchesPanel())
    expect(panel.getByText('traffic.csv')).toBeInTheDocument()
    expect(panel.getByText('1,234')).toBeInTheDocument()
    expect(panel.getByText('pending')).toBeInTheDocument()
    expect(panel.getByText(/not analyzed/)).toBeInTheDocument()
    expect(panel.getByText('3f2b8c1e-6a4d-4e0b-9d7a-2c5f1a8b9e30')).toBeInTheDocument()
    expect(panel.queryByText('No batches registered yet')).not.toBeInTheDocument()
  })

  it('updates the page summary to say traffic is registered but not analyzed', async () => {
    stubFetch(() => Promise.resolve(jsonResponse(batchResponse({ total_records: 1234 }), 201)))
    render(<CommandCenterPage apiHealth={online} />)

    await uploadCsv()

    expect(screen.getByText(/1 batch registered this session \(1,234 records\)/)).toBeInTheDocument()
    expect(screen.getByText(/The traffic has not been analyzed yet/, { selector: 'p.mt-6' })).toBeInTheDocument()
    expect(screen.queryByText(/No network-flow data has been ingested yet/)).not.toBeInTheDocument()
  })

  it('lists several batches newest first', async () => {
    let call = 0
    stubFetch(() => {
      call += 1
      return Promise.resolve(
        jsonResponse(batchResponse({ batch_id: `00000000-0000-4000-8000-00000000000${call}`, filename: `capture-${call}.csv` }), 201),
      )
    })
    render(<CommandCenterPage apiHealth={online} />)
    await uploadCsv(csvFile('capture-1.csv'))
    await userEvent.click(screen.getByRole('button', { name: 'Upload another file' }))

    await uploadCsv(csvFile('capture-2.csv'))

    const names = within(batchesPanel()).getAllByText(/^capture-\d\.csv$/).map((node) => node.textContent)
    expect(names).toEqual(['capture-2.csv', 'capture-1.csv'])
    expect(screen.getByText(/2 batches registered this session/)).toBeInTheDocument()
  })

  it('introduces no fake security metrics after an upload', async () => {
    stubFetch(() => Promise.resolve(jsonResponse(batchResponse(), 201)))
    render(<CommandCenterPage apiHealth={online} />)

    await uploadCsv()

    const telemetry = screen.getByRole('region', { name: 'Security telemetry' })
    expect(within(telemetry).getAllByText('No data')).toHaveLength(4)
    expect(telemetry.textContent).not.toMatch(/\d/)
    expect(document.body.textContent).not.toMatch(/\d+(\.\d+)?\s?%/)
    expect(telemetry).toHaveTextContent('No batch has been analyzed yet')
  })

  it('keeps showing registered batches if the API later becomes unreachable', async () => {
    stubFetch(() => Promise.resolve(jsonResponse(batchResponse(), 201)))
    const { rerender } = render(<CommandCenterPage apiHealth={online} />)
    await uploadCsv()

    rerender(<CommandCenterPage apiHealth={offline} />)

    expect(within(batchesPanel()).getByText('traffic.csv')).toBeInTheDocument()
    expect(within(batchesPanel()).queryByRole('alert')).not.toBeInTheDocument()
  })

  it('still reports an unreachable API instead of "no batches" when nothing is registered', () => {
    render(<CommandCenterPage apiHealth={offline} />)

    expect(within(batchesPanel()).getByRole('alert')).toHaveTextContent('AI-IDS backend unavailable')
  })
})

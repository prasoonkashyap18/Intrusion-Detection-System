import { Inbox } from 'lucide-react'
import { EmptyState, LoadingState, OfflineState } from '../../components/states'
import { Panel } from '../../components/ui/Panel'
import { SectionHeading } from '../../components/ui/SectionHeading'
import { StatusDot } from '../../components/ui/StatusDot'
import type { ApiHealth } from '../../hooks/useApiHealth'
import type { UploadBatchResponse } from '../../types/detection'
import { cn } from '../../utils/cn'
import { formatCount, formatUtcDateTime } from '../../utils/format'

interface DetectionBatchesPanelProps {
  apiHealth: ApiHealth
  /** Batches registered in this browser session. There is no list endpoint yet. */
  batches: UploadBatchResponse[]
  className?: string
}

export function DetectionBatchesPanel({ apiHealth, batches, className }: DetectionBatchesPanelProps) {
  return (
    <Panel
      interaction="spotlight"
      aria-labelledby="detection-batches-heading"
      className={cn('flex flex-col p-6 lg:p-7', className)}
    >
      <SectionHeading
        id="detection-batches-heading"
        title="Detection batches"
        description="Batches registered in this browser session. Stored batch history is not available yet."
      />
      {batches.length > 0 ? <BatchList batches={batches} /> : <EmptyBatches apiHealth={apiHealth} />}
    </Panel>
  )
}

function BatchList({ batches }: { batches: UploadBatchResponse[] }) {
  return (
    <ul className="mt-6 divide-y divide-graphite-900/6">
      {batches.map((batch) => (
        <li key={batch.batch_id} className="flex flex-col gap-1.5 py-3.5 first:pt-0 last:pb-0">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
            <p className="min-w-0 break-all text-sm font-medium text-graphite-900">{batch.filename}</p>
            <span className="inline-flex shrink-0 items-center gap-2 text-[13px] text-graphite-700">
              <StatusDot tone="ice" />
              <span className="capitalize">{batch.status}</span>
              <span className="text-graphite-500">· not analyzed</span>
            </span>
          </div>
          <p className="text-[13px] text-graphite-500">
            <span className="tabular-nums">{formatCount(batch.total_records)}</span> records ·{' '}
            <span className="font-mono text-xs tabular-nums">{formatUtcDateTime(batch.created_at)}</span>
          </p>
          <p className="font-mono text-xs break-all text-graphite-500">{batch.batch_id}</p>
        </li>
      ))}
    </ul>
  )
}

/**
 * "Nothing to show" is only honest once the backend has answered. While the
 * health check is in flight, or when it cannot be reached, this says so
 * instead of claiming there are no batches.
 */
function EmptyBatches({ apiHealth }: { apiHealth: ApiHealth }) {
  if (apiHealth.status === 'connecting') {
    return <LoadingState title="Checking for batches" description="Contacting the AI-IDS API." className="flex-1" />
  }

  if (apiHealth.status === 'offline') {
    return (
      <OfflineState
        description="Detection batches cannot be loaded while the API is unreachable. Check that the FastAPI service is running, then try again."
        onRetry={apiHealth.recheck}
        className="flex-1"
      />
    )
  }

  return (
    <EmptyState
      icon={Inbox}
      title="No batches registered yet"
      description="Upload a network-flow CSV and its batch will appear here, pending analysis."
      className="flex-1"
    />
  )
}

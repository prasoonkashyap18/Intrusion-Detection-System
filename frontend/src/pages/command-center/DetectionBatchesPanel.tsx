import { ChevronLeft, ChevronRight, Inbox, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { BatchDetailDialog } from '../../components/batch/BatchDetailDialog'
import { STATUS_PRESENTATION } from '../../components/batch/batchStatus'
import { EmptyState, ErrorState, LoadingState, OfflineState } from '../../components/states'
import { Button } from '../../components/ui/Button'
import { Panel } from '../../components/ui/Panel'
import { SectionHeading } from '../../components/ui/SectionHeading'
import { StatusDot } from '../../components/ui/StatusDot'
import type { ApiHealth } from '../../hooks/useApiHealth'
import type { DetectionBatches } from '../../hooks/useDetectionBatches'
import type { DetectionBatch } from '../../types/detection'
import { cn } from '../../utils/cn'
import { formatCount, formatLocalDateTime, formatUtcDateTime } from '../../utils/format'

interface DetectionBatchesPanelProps {
  batches: DetectionBatches
  /** Re-checks backend health alongside a reload when the API was unreachable. */
  apiHealth: ApiHealth
  className?: string
}

export function DetectionBatchesPanel({ batches, apiHealth, className }: DetectionBatchesPanelProps) {
  const { data, isLoading, refresh } = batches
  const isRefreshing = isLoading && data !== null
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null)

  return (
    <>
      <Panel
        interaction="spotlight"
        aria-labelledby="detection-batches-heading"
        className={cn('flex flex-col p-6 lg:p-7', className)}
      >
        <SectionHeading
          id="detection-batches-heading"
          title="Detection batches"
          description="Registered batches, newest first, loaded from the database. Select one for details."
          action={
            <Button onClick={refresh} disabled={isLoading}>
              <RefreshCw className={cn('size-3.5', isLoading && 'animate-spin')} aria-hidden="true" />
              Refresh
            </Button>
          }
        />
        <p aria-live="polite" className="sr-only">
          {isRefreshing ? 'Refreshing batches' : ''}
        </p>
        <BatchesContent batches={batches} apiHealth={apiHealth} onSelectBatch={setSelectedBatchId} />
      </Panel>
      {selectedBatchId && <BatchDetailDialog batchId={selectedBatchId} onClose={() => setSelectedBatchId(null)} />}
    </>
  )
}

interface BatchesContentProps {
  batches: DetectionBatches
  apiHealth: ApiHealth
  onSelectBatch: (batchId: string) => void
}

function BatchesContent({ batches, apiHealth, onSelectBatch }: BatchesContentProps) {
  const { data, failure, isLoading, refresh } = batches

  if (!data) {
    if (failure?.unreachable) {
      return (
        <OfflineState
          description="Detection batches cannot be loaded while the API is unreachable. Check that the FastAPI service is running, then try again."
          isRetrying={isLoading}
          onRetry={() => {
            apiHealth.recheck()
            refresh()
          }}
          className="flex-1"
        />
      )
    }
    if (failure) {
      return (
        <ErrorState
          title="Unable to load detection batches"
          message={failure.message}
          onRetry={isLoading ? undefined : refresh}
          className="flex-1"
        />
      )
    }
    return <LoadingState title="Loading detection batches" description="Contacting the AI-IDS API." className="flex-1" />
  }

  return (
    <>
      {failure && (
        <div className="mt-5 rounded-xl border border-graphite-900/8 bg-graphite-50/60 p-4">
          <ErrorState
            compact
            title="Could not refresh"
            message={`${failure.message} The list below may be out of date.`}
            onRetry={isLoading ? undefined : refresh}
          />
        </div>
      )}
      {data.total_items === 0 ? (
        <EmptyState
          icon={Inbox}
          title="No batches registered yet"
          description="Upload a network-flow CSV and its batch will appear here, pending analysis."
          className="flex-1"
        />
      ) : (
        <>
          <ul aria-busy={isLoading} className={cn('mt-6 divide-y divide-graphite-900/6 transition-opacity', isLoading && 'opacity-60')}>
            {data.items.map((batch) => (
              <BatchRow key={batch.batch_id} batch={batch} onSelect={onSelectBatch} />
            ))}
          </ul>
          <BatchPager batches={batches} />
        </>
      )}
    </>
  )
}

function BatchRow({ batch, onSelect }: { batch: DetectionBatch; onSelect: (batchId: string) => void }) {
  const status = STATUS_PRESENTATION[batch.status]

  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(batch.batch_id)}
        aria-label={`View details for ${batch.filename}`}
        className="-mx-2 flex w-[calc(100%+1rem)] flex-col gap-1.5 rounded-lg px-2 py-4 text-left transition-colors duration-150 hover:bg-graphite-900/3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60 focus-visible:ring-offset-2 focus-visible:ring-offset-white"
      >
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
          <p className="min-w-0 flex-1 truncate text-sm font-medium text-graphite-900" title={batch.filename}>
            {batch.filename}
          </p>
          <span className="inline-flex shrink-0 items-center gap-2 text-[13px] text-graphite-700">
            <StatusDot tone={status.tone} />
            <span className="font-medium">{status.label}</span>
            <span className="text-graphite-500">· {status.meaning}</span>
          </span>
        </div>
        <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-[13px] text-graphite-500">
          <span>
            <span className="tabular-nums text-graphite-700">{formatCount(batch.total_records)}</span>{' '}
            {batch.total_records === 1 ? 'record' : 'records'}
          </span>
          <span>
            <span className="tabular-nums text-graphite-700">{formatCount(batch.processed_records)}</span> processed
          </span>
          <span>
            <span className="tabular-nums text-graphite-700">{formatCount(batch.failed_records)}</span> failed
          </span>
          <time dateTime={batch.created_at} title={formatUtcDateTime(batch.created_at)} className="tabular-nums">
            {formatLocalDateTime(batch.created_at)}
          </time>
        </p>
        <p className="truncate font-mono text-xs text-graphite-500" title={batch.batch_id}>
          {batch.batch_id}
        </p>
      </button>
    </li>
  )
}

function BatchPager({ batches }: { batches: DetectionBatches }) {
  const { data, isLoading, page, nextPage, previousPage } = batches
  if (!data) return null

  const summary = `${formatCount(data.total_items)} ${data.total_items === 1 ? 'batch' : 'batches'}`
  if (data.total_pages <= 1) {
    return <p className="mt-5 border-t border-graphite-900/6 pt-4 text-[13px] text-graphite-500">{summary}</p>
  }

  return (
    <nav
      aria-label="Batch pagination"
      className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-graphite-900/6 pt-4"
    >
      <p className="text-[13px] text-graphite-500">
        Page <span className="tabular-nums text-graphite-700">{data.page}</span> of{' '}
        <span className="tabular-nums text-graphite-700">{data.total_pages}</span> · {summary}
      </p>
      <div className="flex gap-2">
        <Button aria-label="Previous page" onClick={previousPage} disabled={isLoading || page <= 1}>
          <ChevronLeft className="size-3.5" aria-hidden="true" />
          Previous
        </Button>
        <Button aria-label="Next page" onClick={nextPage} disabled={isLoading || page >= data.total_pages}>
          Next
          <ChevronRight className="size-3.5" aria-hidden="true" />
        </Button>
      </div>
    </nav>
  )
}

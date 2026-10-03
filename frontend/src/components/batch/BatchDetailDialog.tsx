import { Check, Copy, ShieldQuestion, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useBatchDetail } from '../../hooks/useBatchDetail'
import type { DetectionBatch } from '../../types/detection'
import { formatCount, formatLocalDateTime, formatUtcDateTime } from '../../utils/format'
import { EmptyState, ErrorState, LoadingState } from '../states'
import { Button } from '../ui/Button'
import { StatusDot } from '../ui/StatusDot'
import { STATUS_PRESENTATION } from './batchStatus'

interface BatchDetailDialogProps {
  batchId: string
  onClose: () => void
}

/**
 * Richer single-batch view, opened from a row in the Detection batches list.
 * A native modal <dialog> (as used by MobileNavDrawer): the browser provides
 * focus containment, Escape and backdrop light-dismiss (`closedby="any"`),
 * background inertness, and returns focus to the trigger on close.
 */
export function BatchDetailDialog({ batchId, onClose }: BatchDetailDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const detail = useBatchDetail(batchId)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return undefined

    dialog.addEventListener('close', onClose)
    if (!dialog.open) dialog.showModal()
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    // No dialog.close() here: its `close` event is dispatched asynchronously and
    // would reach the listener of a re-run effect. Unmounting removes the dialog
    // from the top layer anyway.
    return () => {
      dialog.removeEventListener('close', onClose)
      document.body.style.overflow = previousOverflow
    }
  }, [onClose])

  const closeDialog = useCallback(() => dialogRef.current?.close(), [])

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="batch-detail-heading"
      closedby="any"
      className="fixed top-1/2 left-1/2 m-0 max-h-[85vh] w-[min(640px,92vw)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-graphite-900/8 bg-white p-0 text-graphite-800 shadow-raised backdrop:bg-graphite-950/30 backdrop:backdrop-blur-sm"
    >
      <div className="p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="font-mono text-micro uppercase tracking-label text-graphite-500">Detection batch</p>
            <h2 id="batch-detail-heading" className="mt-1 truncate text-lg font-semibold text-graphite-950">
              {detail.data?.filename ?? 'Batch detail'}
            </h2>
          </div>
          <Button variant="ghost" size="icon" aria-label="Close" onClick={closeDialog}>
            <X className="size-4" aria-hidden="true" />
          </Button>
        </div>

        <div className="mt-6">
          {detail.data ? (
            <BatchDetailBody batch={detail.data} />
          ) : detail.failure ? (
            <ErrorState
              title={detail.failure.notFound ? 'Batch not found' : 'Unable to load this batch'}
              message={detail.failure.message}
              onRetry={detail.failure.notFound || detail.isLoading ? undefined : detail.refresh}
            />
          ) : (
            <LoadingState title="Loading batch detail" description="Contacting the AI-IDS API." />
          )}
        </div>
      </div>
    </dialog>
  )
}

function BatchDetailBody({ batch }: { batch: DetectionBatch }) {
  const status = STATUS_PRESENTATION[batch.status]
  const [copied, setCopied] = useState(false)

  const copyBatchId = useCallback(() => {
    navigator.clipboard
      ?.writeText(batch.batch_id)
      .then(() => {
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1500)
      })
      .catch(() => {
        // Clipboard access can be denied by the browser; the ID is still selectable by hand.
      })
  }, [batch.batch_id])

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-2.5 rounded-xl bg-graphite-50/60 px-4 py-3 ring-1 ring-graphite-900/6">
        <StatusDot tone={status.tone} />
        <p className="text-sm text-graphite-800">
          <span className="font-medium">{status.label}</span>
          <span className="text-graphite-500"> · {status.meaning}</span>
        </p>
      </div>

      <dl className="grid grid-cols-3 gap-3">
        <StatTile label="Total records" value={formatCount(batch.total_records)} />
        <StatTile label="Processed" value={formatCount(batch.processed_records)} />
        <StatTile label="Failed" value={formatCount(batch.failed_records)} />
      </dl>

      <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
        <DetailField label="Created">
          <time dateTime={batch.created_at} title={formatUtcDateTime(batch.created_at)}>
            {formatLocalDateTime(batch.created_at)}
          </time>
        </DetailField>
        <DetailField label="Completed">
          {batch.completed_at ? (
            <time dateTime={batch.completed_at} title={formatUtcDateTime(batch.completed_at)}>
              {formatLocalDateTime(batch.completed_at)}
            </time>
          ) : (
            <span className="text-graphite-500">Not yet completed</span>
          )}
        </DetailField>
      </dl>

      <div>
        <p className="text-xs text-graphite-500">Batch ID</p>
        <div className="mt-1.5 flex items-center gap-2 rounded-lg border border-graphite-900/8 bg-graphite-50/60 px-3 py-2">
          <code className="flex-1 min-w-0 truncate select-all font-mono text-xs text-graphite-700">
            {batch.batch_id}
          </code>
          <Button variant="ghost" size="icon" aria-label="Copy batch ID" onClick={copyBatchId}>
            {copied ? (
              <Check className="size-3.5 text-accent-600" aria-hidden="true" />
            ) : (
              <Copy className="size-3.5" aria-hidden="true" />
            )}
          </Button>
        </div>
      </div>

      <div className="border-t border-graphite-900/6 pt-5">
        <EmptyState
          compact
          icon={ShieldQuestion}
          title="No detection results yet"
          description="This batch has not been analyzed. Results will appear here once the detection pipeline exists."
        />
      </div>
    </div>
  )
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-graphite-900/7 bg-white px-3 py-2.5 text-center shadow-control">
      <p className="font-mono text-lg font-medium tabular-nums text-graphite-900">{value}</p>
      <p className="mt-0.5 text-[11px] text-graphite-500">{label}</p>
    </div>
  )
}

function DetailField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-graphite-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-graphite-800">{children}</dd>
    </div>
  )
}

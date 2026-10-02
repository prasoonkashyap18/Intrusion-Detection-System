import { CircleCheck, FileText, FileUp, TriangleAlert } from 'lucide-react'
import { useEffect, useId, useRef, useState, type ChangeEvent, type ReactNode } from 'react'
import { useCsvUpload } from '../../hooks/useCsvUpload'
import { MAX_UPLOAD_BYTES } from '../../services'
import type { UploadBatchResponse } from '../../types/detection'
import { cn } from '../../utils/cn'
import { formatBytes, formatCount, formatUtcDateTime } from '../../utils/format'
import { ErrorState, LoadingState } from '../states'
import { Button } from '../ui/Button'
import { StatusDot } from '../ui/StatusDot'

interface CsvUploadProps {
  /** Called with the batch the backend registered, e.g. to add it to a list. */
  onUploaded?: (batch: UploadBatchResponse) => void
  className?: string
}

function hasFiles(event: DragEvent): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes('Files')
}

/**
 * Select-or-drop CSV upload. Registers the file as a pending batch; it does
 * not analyze it, and nothing here claims otherwise.
 */
export function CsvUpload({ onUploaded, className }: CsvUploadProps) {
  const { phase, file, validationMessage, batch, failure, selectFile, rejectSelection, upload, reset } =
    useCsvUpload({ onUploaded })
  const inputRef = useRef<HTMLInputElement>(null)
  const zoneRef = useRef<HTMLFieldSetElement>(null)
  const inputId = useId()
  const [dragActive, setDragActive] = useState(false)

  const busy = phase === 'uploading'
  const showZone = phase !== 'success' && phase !== 'error'
  const openChooser = () => inputRef.current?.click()

  const handleInputChange = (event: ChangeEvent<HTMLInputElement>) => {
    const picked = event.target.files?.[0]
    // Clearing the value lets the same file be chosen again after an error.
    event.target.value = ''
    if (picked) selectFile(picked)
  }

  // Drag and drop is a pointer-only enhancement scoped to this zone; the
  // "Choose file" button is the keyboard and assistive-technology path. Native
  // listeners keep it off the JSX of a non-interactive element.
  useEffect(() => {
    const zone = zoneRef.current
    if (!zone || !showZone) return undefined

    const handleDrag = (event: DragEvent) => {
      if (busy || !hasFiles(event)) return
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
      setDragActive(true)
    }
    const handleDragLeave = (event: DragEvent) => {
      const next = event.relatedTarget
      if (!(next instanceof Node) || !zone.contains(next)) setDragActive(false)
    }
    const handleDrop = (event: DragEvent) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      setDragActive(false)
      const files = event.dataTransfer?.files
      if (busy || !files) return
      if (files.length === 1 && files[0]) selectFile(files[0])
      else if (files.length > 1) rejectSelection('Drop a single CSV file.')
    }

    zone.addEventListener('dragenter', handleDrag)
    zone.addEventListener('dragover', handleDrag)
    zone.addEventListener('dragleave', handleDragLeave)
    zone.addEventListener('drop', handleDrop)
    return () => {
      zone.removeEventListener('dragenter', handleDrag)
      zone.removeEventListener('dragover', handleDrag)
      zone.removeEventListener('dragleave', handleDragLeave)
      zone.removeEventListener('drop', handleDrop)
    }
  }, [busy, showZone, selectFile, rejectSelection])

  return (
    <div className={className}>
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        accept=".csv,text/csv"
        aria-label="Choose a CSV file"
        hidden
        onChange={handleInputChange}
      />

      {phase === 'success' && batch ? (
        <UploadSuccess batch={batch} onUploadAnother={reset} />
      ) : phase === 'error' && failure ? (
        <div className="rounded-xl border border-graphite-900/8 bg-graphite-50/60 p-4">
          <ErrorState
            compact
            title="Upload failed"
            message={failure.message}
            onRetry={failure.retryable ? upload : undefined}
            actions={
              <Button variant="ghost" onClick={reset}>
                Choose a different file
              </Button>
            }
          />
        </div>
      ) : (
        <fieldset
          ref={zoneRef}
          data-drag-active={dragActive ? '' : undefined}
          className={cn(
            'm-0 block min-w-0 rounded-xl border border-dashed px-5 py-8 text-center transition-[border-color,background-color] duration-200',
            'border-graphite-300 bg-graphite-50/60 data-drag-active:border-accent-500 data-drag-active:bg-accent-50',
          )}
        >
          <legend className="sr-only">CSV upload area</legend>
          {file ? (
            <SelectedFile
              file={file}
              uploading={busy}
              onUpload={upload}
              onReplace={openChooser}
            />
          ) : (
            <div className="flex flex-col items-center gap-4">
              <span className="grid size-12 place-items-center rounded-2xl bg-linear-to-b from-white to-graphite-50 text-graphite-500 shadow-control ring-1 ring-graphite-900/7">
                <FileUp className="size-5" strokeWidth={1.6} aria-hidden="true" />
              </span>
              <div>
                <p className="text-sm font-medium text-graphite-900">
                  {dragActive ? 'Release to select this file' : 'Upload network traffic'}
                </p>
                <p className="mx-auto mt-1.5 max-w-xs text-[13px] leading-relaxed text-graphite-500">
                  Drop a CSV here or choose a file.
                </p>
              </div>
              <Button variant="primary" size="md" onClick={openChooser}>
                Choose file
              </Button>
              <p className="text-xs text-graphite-500">
                Comma-separated · up to {formatBytes(MAX_UPLOAD_BYTES)} · registered as a batch, not analyzed yet
              </p>
            </div>
          )}

          {busy && (
            <LoadingState
              compact
              className="mt-5 justify-center"
              title="Uploading…"
              description="Sending the file to the AI-IDS API. Nothing is analyzed yet."
            />
          )}
        </fieldset>
      )}

      {validationMessage && phase !== 'success' && phase !== 'error' && (
        <p role="alert" className="mt-3 flex items-start gap-2 text-[13px] leading-relaxed text-graphite-700">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-accent-700" strokeWidth={1.8} aria-hidden="true" />
          <span>
            <span className="font-medium">File not accepted. </span>
            {validationMessage}
          </span>
        </p>
      )}
    </div>
  )
}

interface SelectedFileProps {
  file: File
  uploading: boolean
  onUpload: () => void
  onReplace: () => void
}

function SelectedFile({ file, uploading, onUpload, onReplace }: SelectedFileProps) {
  return (
    <div className="flex flex-col items-center gap-5">
      <div className="flex w-full max-w-sm items-center gap-3 rounded-xl bg-white px-4 py-3 text-left shadow-control ring-1 ring-graphite-900/7">
        <FileText className="size-5 shrink-0 text-graphite-500" strokeWidth={1.6} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-graphite-900" title={file.name}>
            {file.name}
          </p>
          <p className="text-xs text-graphite-500">{formatBytes(file.size)}</p>
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <Button variant="primary" size="md" onClick={onUpload} disabled={uploading}>
          {uploading ? 'Uploading…' : 'Upload CSV'}
        </Button>
        <Button size="md" onClick={onReplace} disabled={uploading}>
          Replace file
        </Button>
      </div>
    </div>
  )
}

function UploadSuccess({ batch, onUploadAnother }: { batch: UploadBatchResponse; onUploadAnother: () => void }) {
  return (
    <output className="block rounded-xl border border-graphite-900/8 bg-graphite-50/60 p-5">
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-accent-50 text-accent-700 ring-1 ring-accent-200">
          <CircleCheck className="size-[18px]" strokeWidth={1.8} aria-hidden="true" />
        </span>
        <div>
          <p className="text-sm font-medium text-graphite-950">Traffic uploaded</p>
          <p className="mt-0.5 text-[13px] leading-relaxed text-graphite-500">
            Batch registered successfully. The traffic has not been analyzed yet.
          </p>
        </div>
      </div>

      <dl className="mt-5 grid gap-x-6 gap-y-3 text-[13px] sm:grid-cols-2">
        <Detail label="Batch ID">
          <span className="font-mono text-xs break-all">{batch.batch_id}</span>
        </Detail>
        <Detail label="File">
          <span className="break-all">{batch.filename}</span>
        </Detail>
        <Detail label="Records">
          <span className="tabular-nums">{formatCount(batch.total_records)}</span>
        </Detail>
        <Detail label="Status">
          <span className="inline-flex items-center gap-2 capitalize">
            <StatusDot tone="ice" />
            {batch.status}
          </span>
        </Detail>
        <Detail label="Registered">
          <span className="font-mono text-xs tabular-nums">{formatUtcDateTime(batch.created_at)}</span>
        </Detail>
      </dl>

      <div className="mt-5">
        <Button onClick={onUploadAnother}>Upload another file</Button>
      </div>
    </output>
  )
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-graphite-500">{label}</dt>
      <dd className="mt-0.5 text-graphite-900">{children}</dd>
    </div>
  )
}

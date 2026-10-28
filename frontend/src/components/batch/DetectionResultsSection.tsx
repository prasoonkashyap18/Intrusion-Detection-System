import { ChevronLeft, ChevronRight, ShieldQuestion } from 'lucide-react'
import { useDetectionResults, type DetectionResults } from '../../hooks/useDetectionResults'
import type { DetectionResult } from '../../types/detection'
import { cn } from '../../utils/cn'
import { formatCount, formatLocalDateTime, formatPercent, formatUtcDateTime } from '../../utils/format'
import { EmptyState, ErrorState, LoadingState } from '../states'
import { Button } from '../ui/Button'
import { NoData } from '../ui/NoData'
import { SectionHeading } from '../ui/SectionHeading'
import { StatusDot } from '../ui/StatusDot'
import { PREDICTION_PRESENTATION } from './predictionStatus'

interface DetectionResultsSectionProps {
  batchId: string
}

/**
 * This batch's own persisted predictions (Steps 26-28): real model output
 * for rows the backend actually processed, read-only. Never implies a
 * prediction is a guaranteed classification of the underlying traffic.
 */
export function DetectionResultsSection({ batchId }: DetectionResultsSectionProps) {
  const results = useDetectionResults(batchId)

  return (
    <div>
      <SectionHeading
        id="detection-results-heading"
        title="Detection results"
        description="Model predictions for this batch's rows — not a guarantee of the underlying traffic's actual behavior."
      />
      <div className="mt-4">
        <DetectionResultsContent results={results} />
      </div>
    </div>
  )
}

function DetectionResultsContent({ results }: { results: DetectionResults }) {
  const { data, failure, isLoading, refresh } = results

  if (!data) {
    if (failure) {
      return (
        <ErrorState
          compact
          title={failure.notFound ? 'No detection results' : 'Unable to load detection results'}
          message={failure.message}
          onRetry={failure.notFound || isLoading ? undefined : refresh}
        />
      )
    }
    return <LoadingState compact title="Loading detection results" description="Contacting the AI-IDS API." />
  }

  if (data.total_items === 0) {
    return (
      <EmptyState
        compact
        icon={ShieldQuestion}
        title="No detection results yet"
        description="This batch has not produced any detection results. Results appear here once a compatible trained model has run against it."
      />
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {failure && (
        <p role="alert" className="text-xs text-accent-700">
          {failure.message} The list below may be out of date.
        </p>
      )}
      <ul
        aria-busy={isLoading}
        className={cn('divide-y divide-graphite-900/6 transition-opacity', isLoading && 'opacity-60')}
      >
        {data.items.map((result) => (
          <DetectionResultRow key={result.id} result={result} />
        ))}
      </ul>
      <ResultsPager results={results} />
    </div>
  )
}

function DetectionResultRow({ result }: { result: DetectionResult }) {
  const presentation = PREDICTION_PRESENTATION[result.prediction_name]

  return (
    <li className="flex flex-col gap-1.5 py-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <span className="flex items-center gap-3">
          <span className="font-mono text-xs tabular-nums text-graphite-500" title={`Original row ${result.row_number}`}>
            Row {formatCount(result.row_number)}
          </span>
          <span className="inline-flex items-center gap-2">
            <StatusDot tone={presentation.tone} />
            <span className="text-sm font-medium text-graphite-900">{presentation.label}</span>
          </span>
        </span>
        <time
          dateTime={result.created_at}
          title={formatUtcDateTime(result.created_at)}
          className="shrink-0 text-[13px] tabular-nums text-graphite-500"
        >
          {formatLocalDateTime(result.created_at)}
        </time>
      </div>
      <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-[13px] text-graphite-500">
        <span className="text-graphite-500">
          {presentation.meaning} ·{' '}
          {result.attack_probability === null ? (
            <NoData />
          ) : (
            <span className="tabular-nums text-graphite-700">{formatPercent(result.attack_probability)}</span>
          )}{' '}
          attack probability
        </span>
        <span className="truncate" title={`Model ${result.model_name} version ${result.model_version}`}>
          {result.model_name} <span className="text-graphite-400">v{result.model_version}</span>
        </span>
      </p>
    </li>
  )
}

function ResultsPager({ results }: { results: DetectionResults }) {
  const { data, isLoading, page, nextPage, previousPage } = results
  if (!data) return null

  const summary = `${formatCount(data.total_items)} ${data.total_items === 1 ? 'result' : 'results'}`
  if (data.total_pages <= 1) {
    return <p className="mt-2 border-t border-graphite-900/6 pt-3 text-[13px] text-graphite-500">{summary}</p>
  }

  return (
    <nav
      aria-label="Detection result pagination"
      className="mt-2 flex flex-wrap items-center justify-between gap-3 border-t border-graphite-900/6 pt-3"
    >
      <p className="text-[13px] text-graphite-500">
        Page <span className="tabular-nums text-graphite-700">{data.page}</span> of{' '}
        <span className="tabular-nums text-graphite-700">{data.total_pages}</span> · {summary}
      </p>
      <div className="flex gap-2">
        <Button aria-label="Previous page of detection results" onClick={previousPage} disabled={isLoading || page <= 1}>
          <ChevronLeft className="size-3.5" aria-hidden="true" />
          Previous
        </Button>
        <Button
          aria-label="Next page of detection results"
          onClick={nextPage}
          disabled={isLoading || page >= data.total_pages}
        >
          Next
          <ChevronRight className="size-3.5" aria-hidden="true" />
        </Button>
      </div>
    </nav>
  )
}

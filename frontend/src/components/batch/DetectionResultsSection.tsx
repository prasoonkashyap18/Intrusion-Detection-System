import { ChevronLeft, ChevronRight, Search, ShieldQuestion, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useDetectionResults, type DetectionResults } from '../../hooks/useDetectionResults'
import type { DetectionResult, PredictionName } from '../../types/detection'
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

type LabelFilter = 'all' | PredictionName

const LABEL_FILTERS: { value: LabelFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'benign', label: 'Benign' },
  { value: 'attack', label: 'Attack' },
]

/**
 * This batch's own persisted predictions (Steps 26-28): real model output
 * for rows the backend actually processed, read-only. Never implies a
 * prediction is a guaranteed classification of the underlying traffic.
 *
 * Step 30 adds a label filter and a search box over this data. Both apply
 * only to the already-loaded page: the backend's `GET .../results`
 * endpoint has no filter/search parameters of its own (see backend/
 * README.md's "DetectionResult API" — pagination is its only query
 * surface), so filtering/searching the full result set across pages would
 * either require fetching everything into memory (defeating the point of
 * server-side pagination) or silently misreporting the real totals.
 * Instead the page-scoped nature of filtering is stated explicitly in the
 * UI, and the pager below always reflects the backend's own real
 * page/total numbers, never a filtered count.
 */
export function DetectionResultsSection({ batchId }: DetectionResultsSectionProps) {
  const results = useDetectionResults(batchId)
  const [labelFilter, setLabelFilter] = useState<LabelFilter>('all')
  const [query, setQuery] = useState('')

  return (
    <div>
      <SectionHeading
        id="detection-results-heading"
        title="Detection results"
        description="Model predictions for this batch's rows — not a guarantee of the underlying traffic's actual behavior."
      />
      {results.data && results.data.total_items > 0 && (
        <DetectionResultsToolbar labelFilter={labelFilter} onLabelFilterChange={setLabelFilter} query={query} onQueryChange={setQuery} />
      )}
      <div className="mt-4">
        <DetectionResultsContent results={results} labelFilter={labelFilter} query={query} />
      </div>
    </div>
  )
}

interface ToolbarProps {
  labelFilter: LabelFilter
  onLabelFilterChange: (value: LabelFilter) => void
  query: string
  onQueryChange: (value: string) => void
}

function DetectionResultsToolbar({ labelFilter, onLabelFilterChange, query, onQueryChange }: ToolbarProps) {
  return (
    <div className="mt-4 flex flex-wrap items-center gap-3">
      <fieldset className="flex items-center gap-1.5 border-0 p-0 m-0">
        <legend className="sr-only">Filter by prediction</legend>
        {LABEL_FILTERS.map((option) => (
          <Button
            key={option.value}
            aria-pressed={labelFilter === option.value}
            variant={labelFilter === option.value ? 'primary' : 'secondary'}
            onClick={() => onLabelFilterChange(option.value)}
          >
            {option.label}
          </Button>
        ))}
      </fieldset>
      <label className="relative flex-1 min-w-[180px]">
        <span className="sr-only">Search detection results by row number or model</span>
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-graphite-400" aria-hidden="true" />
        <input
          type="search"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Search by row number or model…"
          className="h-8 w-full rounded-lg border border-graphite-900/8 bg-white py-1.5 pr-8 pl-8 text-xs text-graphite-800 shadow-control placeholder:text-graphite-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60"
        />
        {query && (
          <button
            type="button"
            aria-label="Clear search"
            onClick={() => onQueryChange('')}
            className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded p-0.5 text-graphite-400 hover:text-graphite-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60"
          >
            <X className="size-3.5" aria-hidden="true" />
          </button>
        )}
      </label>
    </div>
  )
}

function matchesQuery(result: DetectionResult, query: string): boolean {
  if (!query.trim()) return true
  const needle = query.trim().toLowerCase()
  return (
    String(result.row_number).includes(needle) ||
    result.model_name.toLowerCase().includes(needle) ||
    result.model_version.toLowerCase().includes(needle)
  )
}

interface ContentProps {
  results: DetectionResults
  labelFilter: LabelFilter
  query: string
}

function DetectionResultsContent({ results, labelFilter, query }: ContentProps) {
  const { data, failure, isLoading, refresh } = results

  const filteredItems = useMemo(() => {
    if (!data) return []
    return data.items.filter(
      (result) => (labelFilter === 'all' || result.prediction_name === labelFilter) && matchesQuery(result, query),
    )
  }, [data, labelFilter, query])

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

  const filterActive = labelFilter !== 'all' || query.trim() !== ''

  return (
    <div className="flex flex-col gap-3">
      {failure && (
        <p role="alert" className="text-xs text-accent-700">
          {failure.message} The list below may be out of date.
        </p>
      )}
      {filterActive && (
        <p aria-live="polite" className="text-[13px] text-graphite-500">
          Showing <span className="tabular-nums text-graphite-700">{formatCount(filteredItems.length)}</span> of{' '}
          <span className="tabular-nums text-graphite-700">{formatCount(data.items.length)}</span> result
          {data.items.length === 1 ? '' : 's'} on this page matching the current filter.
        </p>
      )}
      {filteredItems.length === 0 ? (
        <p className="rounded-lg border border-graphite-900/8 bg-graphite-50/60 px-4 py-6 text-center text-[13px] text-graphite-500">
          No results on this page match the current filter.
          {data.total_pages > 1 && ' Other pages may still have matches — try Previous/Next below.'}
        </p>
      ) : (
        <ul
          aria-busy={isLoading}
          className={cn('divide-y divide-graphite-900/6 transition-opacity', isLoading && 'opacity-60')}
        >
          {filteredItems.map((result) => (
            <DetectionResultRow key={result.id} result={result} />
          ))}
        </ul>
      )}
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

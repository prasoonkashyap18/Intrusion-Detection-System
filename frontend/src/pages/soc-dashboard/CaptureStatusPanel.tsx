import { Radio } from 'lucide-react'
import { EmptyState, ErrorState, LoadingState } from '../../components/states'
import { SectionHeading } from '../../components/ui/SectionHeading'
import { StatusDot } from '../../components/ui/StatusDot'
import { useCaptureSessions } from '../../hooks/useCaptureSessions'
import type { CaptureSession } from '../../types/capture'
import { formatCount, formatLocalDateTime, formatUtcDateTime } from '../../utils/format'

const STATUS_TONE: Record<CaptureSession['status'], 'accent' | 'muted' | 'critical'> = {
  running: 'accent',
  stopped: 'muted',
  failed: 'critical',
}

/**
 * Live-monitoring and capture status (Step 45 point 5): reads the real
 * `CaptureSession` rows the backend's Step 42 live-capture service
 * persists — a host with no capture backend installed (see backend/
 * README.md's "Live Traffic Capture" limitations) genuinely shows
 * `failed` sessions with their real error message here, never a
 * fabricated "monitoring active" state.
 */
export function CaptureStatusPanel() {
  const { sessions, failure, isLoading, refresh } = useCaptureSessions()

  return (
    <div>
      <SectionHeading id="capture-status-heading" title="Live monitoring status" description="Capture sessions this server has started, running or finished." />
      <div className="mt-4">
        {sessions ? (
          <CaptureList sessions={sessions} />
        ) : failure ? (
          <ErrorState compact title="Unable to load capture status" message={failure.message} onRetry={isLoading ? undefined : refresh} />
        ) : (
          <LoadingState compact title="Loading capture status" description="Contacting the AI-IDS API." />
        )}
      </div>
    </div>
  )
}

function CaptureList({ sessions }: { sessions: CaptureSession[] }) {
  if (sessions.length === 0) {
    return (
      <EmptyState
        compact
        icon={Radio}
        title="No capture sessions"
        description="No live packet capture has been started on this server yet."
      />
    )
  }

  return (
    <ul className="divide-y divide-graphite-900/6">
      {sessions.slice(0, 5).map((session) => (
        <li key={session.id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2.5">
          <span className="flex min-w-0 items-center gap-2.5">
            <StatusDot tone={STATUS_TONE[session.status]} pulse={session.status === 'running'} />
            <span className="truncate text-sm font-medium text-graphite-900">{session.interface_name}</span>
            <span className="text-xs text-graphite-500 capitalize">{session.status}</span>
          </span>
          <span className="text-[13px] text-graphite-500">
            {formatCount(session.stats.packets_processed ?? 0)} packets ·{' '}
            {formatCount(session.stats.flows_finalized ?? 0)} flows
          </span>
          <time dateTime={session.started_at} title={formatUtcDateTime(session.started_at)} className="shrink-0 text-[13px] tabular-nums text-graphite-500">
            {formatLocalDateTime(session.started_at)}
          </time>
        </li>
      ))}
    </ul>
  )
}

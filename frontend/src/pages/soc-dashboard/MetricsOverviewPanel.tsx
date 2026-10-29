import { useDashboardOverview } from '../../hooks/useDashboardOverview'
import { formatCount } from '../../utils/format'
import { ErrorState, LoadingState } from '../../components/states'
import { Panel } from '../../components/ui/Panel'
import { SectionHeading } from '../../components/ui/SectionHeading'
import { StatusDot } from '../../components/ui/StatusDot'

/**
 * Security overview metrics (Step 45): every number here is a real
 * COUNT from the backend's GET /api/v1/dashboard/overview — never an
 * estimate, and an empty system genuinely shows zeros rather than a
 * placeholder figure.
 */
export function MetricsOverviewPanel() {
  const overview = useDashboardOverview()

  return (
    <div>
      <SectionHeading id="overview-heading" title="Security overview" description="Real, measured totals from persisted detection and alert records." />
      <div className="mt-4">
        {overview.data ? (
          <MetricsGrid overview={overview.data} />
        ) : overview.failure ? (
          <ErrorState compact title="Unable to load overview metrics" message={overview.failure.message} onRetry={overview.isLoading ? undefined : overview.refresh} />
        ) : (
          <LoadingState compact title="Loading overview metrics" description="Contacting the AI-IDS API." />
        )}
      </div>
    </div>
  )
}

function MetricsGrid({ overview }: { overview: NonNullable<ReturnType<typeof useDashboardOverview>['data']> }) {
  const severity = overview.alert_severity_distribution
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <MetricCard label="Batches processed" value={overview.total_batches} />
      <MetricCard label="Detection results" value={overview.total_detection_results} />
      <MetricCard label="Anomaly results" value={overview.total_anomaly_results} />
      <MetricCard label="Total alerts" value={overview.total_alerts} />
      <MetricCard label="Open alerts" value={overview.open_alerts} emphasize={overview.open_alerts > 0} />
      <MetricCard label="Active captures" value={overview.active_capture_sessions} />

      <Panel className="col-span-full p-4 sm:col-span-3 lg:col-span-6">
        <p className="text-xs font-medium text-graphite-500">Alert severity distribution</p>
        <div className="mt-3 flex flex-wrap gap-4">
          <SeverityCount label="Critical" tone="critical" value={severity.critical} />
          <SeverityCount label="High" tone="high" value={severity.high} />
          <SeverityCount label="Medium" tone="medium" value={severity.medium} />
          <SeverityCount label="Low" tone="low" value={severity.low} />
        </div>
      </Panel>
    </div>
  )
}

function MetricCard({ label, value, emphasize = false }: { label: string; value: number; emphasize?: boolean }) {
  return (
    <Panel className="p-4">
      <p className="text-xs text-graphite-500">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${emphasize ? 'text-accent-600' : 'text-graphite-950'}`}>
        {formatCount(value)}
      </p>
    </Panel>
  )
}

function SeverityCount({ label, tone, value }: { label: string; tone: 'critical' | 'high' | 'medium' | 'low'; value: number }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-graphite-800">
      <StatusDot tone={tone} halo />
      {label} <span className="tabular-nums font-medium text-graphite-950">{formatCount(value)}</span>
    </span>
  )
}

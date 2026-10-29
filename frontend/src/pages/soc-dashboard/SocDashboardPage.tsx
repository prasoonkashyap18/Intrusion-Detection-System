import { Panel } from '../../components/ui/Panel'
import { AlertsSection } from './AlertsSection'
import { CaptureStatusPanel } from './CaptureStatusPanel'
import { MetricsOverviewPanel } from './MetricsOverviewPanel'

/**
 * SOC Dashboard (Step 45): security overview metrics, recent alerts
 * with filtering/search/pagination and an investigation drawer, and
 * live-monitoring status — all backed by real backend data (Steps
 * 26-44). Nothing here fabricates a metric, an alert, or a capture
 * state; each section reports its own loading/empty/error/offline
 * state independently, so one section's failure never hides another's
 * real data.
 */
export function SocDashboardPage() {
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-8 px-5 py-8 md:px-8 lg:px-12">
      <header>
        <p className="font-mono text-micro uppercase tracking-label text-graphite-500">Security Operations</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-graphite-950">SOC Dashboard</h1>
      </header>

      <Panel className="p-5">
        <MetricsOverviewPanel />
      </Panel>

      <Panel className="p-5">
        <AlertsSection />
      </Panel>

      <Panel className="p-5">
        <CaptureStatusPanel />
      </Panel>
    </div>
  )
}

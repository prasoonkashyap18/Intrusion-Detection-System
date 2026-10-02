import { Activity, Gauge, Info, ShieldAlert, Siren } from 'lucide-react'
import { TopologyViewport } from '../../components/topology/TopologyViewport'
import type { ApiHealth } from '../../hooks/useApiHealth'
import { DetectionBatchesPanel } from './DetectionBatchesPanel'
import { DetectionPipelinePanel } from './DetectionPipelinePanel'
import { SeverityScalePanel } from './SeverityScalePanel'
import { SystemStatusPanel } from './SystemStatusPanel'
import { TelemetryCard, type TelemetryMetric } from './TelemetryCard'

const TELEMETRY_METRICS: TelemetryMetric[] = [
  { id: 'flows', label: 'Flows analyzed', icon: Activity, emptyNote: 'Awaiting first upload' },
  { id: 'threats', label: 'Threats detected', icon: ShieldAlert, emptyNote: 'Awaiting detection results' },
  {
    id: 'critical',
    label: 'Critical severity',
    icon: Siren,
    emptyNote: 'Awaiting detection results',
    severity: 'critical',
  },
  { id: 'model', label: 'Model performance', icon: Gauge, emptyNote: 'Awaiting model evaluation' },
]

export function CommandCenterPage({ apiHealth }: { apiHealth: ApiHealth }) {
  return (
    <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-6 px-4 py-6 md:px-6 lg:gap-8 lg:px-8 lg:py-8">
      <header className="max-w-3xl">
        <p className="flex items-center gap-3 font-mono text-micro uppercase tracking-label text-accent before:h-px before:w-6 before:bg-accent/60">
          Command Center
        </p>
        <h1 className="mt-3 text-[1.75rem] font-semibold tracking-tight text-ink-50 md:text-display">
          Network security overview
        </h1>
        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-ink-300">
          Classify uploaded network-flow records with a machine-learning detection pipeline, then review
          predictions, confidence and severity in one place.
        </p>
      </header>

      <div
        role="note"
        className="flex items-start gap-3 rounded-xl border border-ice/15 bg-ice/4 px-4 py-3 text-sm leading-relaxed text-ink-300"
      >
        <Info className="mt-0.5 size-4 shrink-0 text-ice" aria-hidden="true" />
        <p>
          <span className="font-medium text-ink-100">No network-flow data has been ingested yet.</span> The
          panels below are structural placeholders and will populate from real detection results once batches
          are processed.
        </p>
      </div>

      <section aria-labelledby="telemetry-heading">
        <h2 id="telemetry-heading" className="sr-only">
          Detection telemetry
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {TELEMETRY_METRICS.map((metric) => (
            <TelemetryCard key={metric.id} metric={metric} />
          ))}
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-12">
        <TopologyViewport className="xl:col-span-8" />
        <div className="flex flex-col gap-4 xl:col-span-4">
          <SystemStatusPanel apiHealth={apiHealth} />
          <DetectionBatchesPanel className="flex-1" />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-12">
        <DetectionPipelinePanel className="lg:col-span-7" />
        <SeverityScalePanel className="lg:col-span-5" />
      </div>
    </div>
  )
}

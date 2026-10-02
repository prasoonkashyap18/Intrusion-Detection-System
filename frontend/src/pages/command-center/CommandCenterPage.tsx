import { Activity, Gauge, ShieldAlert, Siren } from 'lucide-react'
import { TopologyViewport } from '../../components/topology/TopologyViewport'
import { StatusDot } from '../../components/ui/StatusDot'
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
    <div className="mx-auto w-full max-w-[1480px] px-5 pt-10 pb-20 md:px-8 lg:px-12 lg:pt-14">
      <header className="max-w-3xl">
        <p className="inline-flex items-center gap-2.5 font-mono text-micro uppercase tracking-label text-graphite-500">
          <span aria-hidden="true" className="size-1.5 rounded-full bg-accent-500" />
          Operations · Command Center
        </p>
        <h1 className="mt-5 text-[2.125rem] leading-[1.08] font-semibold tracking-[-0.03em] text-graphite-950 md:text-display">
          Network security overview
        </h1>
        <p className="mt-5 max-w-xl text-base leading-relaxed text-graphite-600">
          Classify uploaded network-flow records with a machine-learning detection pipeline, then review
          predictions, confidence and severity from one place.
        </p>
        <p className="mt-6 flex items-start gap-2.5 text-sm text-graphite-500">
          <StatusDot tone="ice" className="mt-1.5" />
          No network-flow data has been ingested yet. Panels show structural placeholders until real detection
          results exist.
        </p>
      </header>

      <TopologyViewport className="mt-10 lg:mt-12" />

      <section aria-labelledby="telemetry-heading" className="mt-14 lg:mt-16">
        <div className="max-w-xl">
          <h2 id="telemetry-heading" className="text-lg font-semibold tracking-tight text-graphite-950">
            Security telemetry
          </h2>
          <p className="mt-1.5 text-sm text-graphite-500">Values appear once detection results exist.</p>
        </div>
        <div className="mt-6 grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4">
          {TELEMETRY_METRICS.map((metric) => (
            <TelemetryCard key={metric.id} metric={metric} />
          ))}
        </div>
      </section>

      <div className="mt-14 grid grid-cols-1 gap-5 lg:mt-16 xl:grid-cols-12">
        <DetectionPipelinePanel className="xl:col-span-8" />
        <SystemStatusPanel apiHealth={apiHealth} className="xl:col-span-4" />
        <DetectionBatchesPanel className="xl:col-span-8" />
        <SeverityScalePanel className="xl:col-span-4" />
      </div>
    </div>
  )
}

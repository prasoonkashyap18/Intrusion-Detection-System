import { BrainCircuit, Database, FileUp, Gauge, ShieldCheck, SlidersHorizontal, type LucideIcon } from 'lucide-react'
import { Panel } from '../../components/ui/Panel'
import { SectionHeading } from '../../components/ui/SectionHeading'
import { StatusDot } from '../../components/ui/StatusDot'
import { cn } from '../../utils/cn'

interface PipelineStage {
  id: string
  name: string
  detail: string
  icon: LucideIcon
}

// Mirrors the MVP data flow in ARCHITECTURE.md.
const PIPELINE_STAGES: PipelineStage[] = [
  { id: 'ingest', name: 'Ingest', detail: 'CSV upload', icon: FileUp },
  { id: 'validate', name: 'Validate', detail: 'Schema checks', icon: ShieldCheck },
  { id: 'preprocess', name: 'Preprocess', detail: 'Feature prep', icon: SlidersHorizontal },
  { id: 'infer', name: 'Infer', detail: 'ML classifier', icon: BrainCircuit },
  { id: 'score', name: 'Score', detail: 'Confidence & severity', icon: Gauge },
  { id: 'store', name: 'Store', detail: 'SQLite results', icon: Database },
]

export function DetectionPipelinePanel({ className }: { className?: string }) {
  return (
    <Panel interaction="spotlight" aria-labelledby="pipeline-heading" className={cn('p-6 lg:p-7', className)}>
      <SectionHeading
        id="pipeline-heading"
        title="Detection pipeline"
        description="The stages every uploaded batch passes through."
        action={
          <span className="inline-flex shrink-0 items-center gap-2 text-[13px] text-graphite-500">
            <StatusDot tone="muted" />
            Not yet active
          </span>
        }
      />

      <div className="relative mt-9">
        <span
          aria-hidden="true"
          className="pointer-events-none absolute top-[46px] right-[8.33%] left-[8.33%] hidden h-px bg-linear-to-r from-transparent via-graphite-900/12 to-transparent xl:block"
        />
        <ol className="relative grid grid-cols-2 gap-x-4 gap-y-9 sm:grid-cols-3 xl:grid-cols-6">
          {PIPELINE_STAGES.map((stage, index) => {
            const Icon = stage.icon
            return (
              <li key={stage.id} className="group flex flex-col items-center text-center">
                <span aria-hidden="true" className="font-mono text-micro tabular-nums text-graphite-500">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <span className="mt-2 grid size-11 place-items-center rounded-xl bg-white text-graphite-600 shadow-control ring-1 ring-graphite-900/8 transition-[translate,box-shadow,color] duration-200 ease-out group-hover:-translate-y-0.5 group-hover:text-accent-600 group-hover:shadow-raised group-hover:ring-accent-500/35">
                  <Icon className="size-[18px]" strokeWidth={1.6} aria-hidden="true" />
                </span>
                <span className="mt-3 text-sm font-medium text-graphite-900">{stage.name}</span>
                <span className="mt-0.5 text-xs text-graphite-500">{stage.detail}</span>
              </li>
            )
          })}
        </ol>
      </div>
    </Panel>
  )
}

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
    <Panel interaction="spotlight" aria-labelledby="pipeline-heading" className={cn('p-5', className)}>
      <SectionHeading
        id="pipeline-heading"
        eyebrow="Pipeline"
        title="Detection pipeline"
        action={
          <span className="inline-flex shrink-0 items-center gap-2 text-xs text-ink-400">
            <StatusDot tone="muted" />
            Not yet active
          </span>
        }
      />

      <div className="relative mt-7">
        <span
          aria-hidden="true"
          className="pointer-events-none absolute top-5 right-[8.33%] left-[8.33%] hidden h-px bg-linear-to-r from-transparent via-white/15 to-transparent xl:block"
        />
        <ol className="relative grid grid-cols-2 gap-x-4 gap-y-6 sm:grid-cols-3 xl:grid-cols-6">
          {PIPELINE_STAGES.map((stage) => {
            const Icon = stage.icon
            return (
              <li key={stage.id} className="group relative flex flex-col items-center text-center">
                <span className="grid size-10 place-items-center rounded-xl border border-white/10 bg-obsidian-800 text-ink-400 transition-[color,border-color,box-shadow,translate] duration-300 ease-out-expo group-hover:-translate-y-0.5 group-hover:border-accent/40 group-hover:text-accent group-hover:shadow-glow">
                  <Icon className="size-[18px]" strokeWidth={1.75} aria-hidden="true" />
                </span>
                <span className="mt-3 text-sm font-medium text-ink-200">{stage.name}</span>
                <span className="mt-0.5 text-xs text-ink-500">{stage.detail}</span>
              </li>
            )
          })}
        </ol>
      </div>

      <p className="mt-6 border-t border-white/6 pt-3 text-xs text-ink-500">
        Stage-level progress appears here while a batch is being processed.
      </p>
    </Panel>
  )
}

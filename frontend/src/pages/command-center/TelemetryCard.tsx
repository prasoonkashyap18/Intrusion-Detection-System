import type { LucideIcon } from 'lucide-react'
import { useId } from 'react'
import { NoData } from '../../components/ui/NoData'
import { Panel } from '../../components/ui/Panel'
import { cn } from '../../utils/cn'

export interface TelemetryMetric {
  id: string
  label: string
  icon: LucideIcon
  /** Why no value is shown yet. Values appear only once real detection data exists. */
  emptyNote: string
  /** Marks a severity-scoped metric; tints the icon only, never the (empty) value. */
  severity?: 'critical'
}

export function TelemetryCard({ metric }: { metric: TelemetryMetric }) {
  const headingId = useId()
  const Icon = metric.icon

  return (
    <Panel as="article" interaction="tilt" aria-labelledby={headingId} className="flex flex-col p-6">
      <div className="flex items-start justify-between gap-3">
        <span className="grid size-10 place-items-center rounded-xl bg-linear-to-b from-white to-graphite-50 shadow-control ring-1 ring-graphite-900/7">
          <Icon
            className={cn('size-[18px]', metric.severity === 'critical' ? 'text-sev-critical' : 'text-graphite-700')}
            strokeWidth={1.6}
            aria-hidden="true"
          />
        </span>
        <span className="rounded-full bg-graphite-50 px-2 py-0.5 text-[11px] font-medium text-graphite-500 ring-1 ring-graphite-900/5">
          Awaiting data
        </span>
      </div>
      <h3 id={headingId} className="mt-8 text-sm font-medium text-graphite-600">
        {metric.label}
      </h3>
      <p className="mt-2 text-metric font-medium tabular-nums text-graphite-300">
        <NoData />
      </p>
      <p className="mt-3 text-[13px] text-graphite-500">{metric.emptyNote}</p>
    </Panel>
  )
}

import type { LucideIcon } from 'lucide-react'
import { useId } from 'react'
import { NoData } from '../../components/ui/NoData'
import { Panel } from '../../components/ui/Panel'
import { StatusDot } from '../../components/ui/StatusDot'
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
    <Panel as="article" interaction="tilt" aria-labelledby={headingId} className="flex flex-col p-5">
      <div className="flex items-center justify-between gap-3">
        <h3 id={headingId} className="font-mono text-micro uppercase tracking-label text-ink-400">
          {metric.label}
        </h3>
        <span className="grid size-8 place-items-center rounded-lg border border-white/8 bg-white/3">
          <Icon
            className={cn('size-4', metric.severity === 'critical' ? 'text-sev-critical/80' : 'text-ink-400')}
            strokeWidth={1.75}
            aria-hidden="true"
          />
        </span>
      </div>
      <p className="mt-7 font-mono text-telemetry font-medium text-ink-600">
        <NoData />
      </p>
      <p className="mt-5 flex items-center gap-2 border-t border-white/6 pt-3 text-xs text-ink-500">
        <StatusDot tone="muted" />
        {metric.emptyNote}
      </p>
    </Panel>
  )
}

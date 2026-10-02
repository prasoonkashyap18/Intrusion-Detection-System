import { Panel } from '../../components/ui/Panel'
import { SectionHeading } from '../../components/ui/SectionHeading'
import { StatusDot } from '../../components/ui/StatusDot'
import type { TrafficLevel } from '../../types/security'
import { cn } from '../../utils/cn'

const LEVELS: { level: TrafficLevel; label: string; barClass: string }[] = [
  { level: 'normal', label: 'Normal', barClass: 'bg-sev-normal/45' },
  { level: 'low', label: 'Low', barClass: 'bg-sev-low' },
  { level: 'medium', label: 'Medium', barClass: 'bg-sev-medium' },
  { level: 'high', label: 'High', barClass: 'bg-sev-high' },
  { level: 'critical', label: 'Critical', barClass: 'bg-sev-critical' },
]

export function SeverityScalePanel({ className }: { className?: string }) {
  return (
    <Panel interaction="spotlight" aria-labelledby="severity-scale-heading" className={cn('p-6 lg:p-7', className)}>
      <SectionHeading
        id="severity-scale-heading"
        title="Severity scale"
        description="Benign traffic stays neutral; detections are graded from low to critical."
      />

      <div aria-hidden="true" className="mt-7 flex gap-1">
        {LEVELS.map(({ level, barClass }) => (
          <span key={level} className={cn('h-1.5 flex-1 rounded-full', barClass)} />
        ))}
      </div>

      <ul className="mt-4 grid grid-cols-5 gap-2">
        {LEVELS.map(({ level, label }) => (
          <li key={level} className="flex items-center gap-1.5 text-xs text-graphite-700">
            <StatusDot tone={level} />
            <span className="truncate">{label}</span>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

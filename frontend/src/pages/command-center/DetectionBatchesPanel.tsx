import { Inbox } from 'lucide-react'
import { EmptyState } from '../../components/ui/EmptyState'
import { Panel } from '../../components/ui/Panel'
import { SectionHeading } from '../../components/ui/SectionHeading'
import { cn } from '../../utils/cn'

export function DetectionBatchesPanel({ className }: { className?: string }) {
  return (
    <Panel
      interaction="spotlight"
      aria-labelledby="detection-batches-heading"
      className={cn('flex flex-col p-5', className)}
    >
      <SectionHeading id="detection-batches-heading" eyebrow="Activity" title="Detection batches" />
      <EmptyState
        icon={Inbox}
        title="No detection batches yet"
        description="Each uploaded network-flow CSV will appear here as a batch with its processing status and results."
        className="flex-1"
      />
    </Panel>
  )
}

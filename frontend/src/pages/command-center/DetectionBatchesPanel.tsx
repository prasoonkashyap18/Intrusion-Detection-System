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
      className={cn('flex flex-col p-6 lg:p-7', className)}
    >
      <SectionHeading
        id="detection-batches-heading"
        title="Detection batches"
        description="Each uploaded network-flow CSV is processed as a batch."
      />
      <EmptyState
        icon={Inbox}
        title="No detection batches yet"
        description="Batches will appear here with their processing status and results once a network-flow CSV has been uploaded."
        className="flex-1"
      />
    </Panel>
  )
}

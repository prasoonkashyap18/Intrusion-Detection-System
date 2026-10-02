import { Inbox } from 'lucide-react'
import { EmptyState, LoadingState, OfflineState } from '../../components/states'
import { Panel } from '../../components/ui/Panel'
import { SectionHeading } from '../../components/ui/SectionHeading'
import type { ApiHealth } from '../../hooks/useApiHealth'
import { cn } from '../../utils/cn'

export function DetectionBatchesPanel({ apiHealth, className }: { apiHealth: ApiHealth; className?: string }) {
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
      <BatchesContent apiHealth={apiHealth} />
    </Panel>
  )
}

/**
 * "Nothing to show" is only honest once the backend has answered. While the
 * health check is in flight, or when it cannot be reached, this says so
 * instead of claiming there are no batches.
 */
function BatchesContent({ apiHealth }: { apiHealth: ApiHealth }) {
  if (apiHealth.status === 'connecting') {
    return <LoadingState title="Checking for batches" description="Contacting the AI-IDS API." className="flex-1" />
  }

  if (apiHealth.status === 'offline') {
    return (
      <OfflineState
        description="Detection batches cannot be loaded while the API is unreachable. Check that the FastAPI service is running, then try again."
        onRetry={apiHealth.recheck}
        className="flex-1"
      />
    )
  }

  return (
    <EmptyState
      icon={Inbox}
      title="No detection batches yet"
      description="Batches will appear here with their processing status and results once a network-flow CSV has been uploaded."
      className="flex-1"
    />
  )
}

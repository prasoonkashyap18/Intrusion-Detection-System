import { CsvUpload } from '../../components/upload/CsvUpload'
import { Panel } from '../../components/ui/Panel'
import { SectionHeading } from '../../components/ui/SectionHeading'
import type { UploadBatchResponse } from '../../types/detection'
import { cn } from '../../utils/cn'

export function UploadPanel({
  onUploaded,
  className,
}: {
  onUploaded: (batch: UploadBatchResponse) => void
  className?: string
}) {
  return (
    <Panel aria-labelledby="upload-heading" className={cn('p-6 lg:p-7', className)}>
      <SectionHeading
        id="upload-heading"
        title="Traffic ingestion"
        description="Register a network-flow CSV as a detection batch."
      />
      <CsvUpload onUploaded={onUploaded} className="mt-6" />
    </Panel>
  )
}

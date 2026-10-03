import type { ProcessingStatus } from '../../types/security'
import type { StatusTone } from '../ui/StatusDot'

export interface StatusPresentation {
  label: string
  /** What the status means, in words, so it never depends on colour. */
  meaning: string
  tone: StatusTone
}

// Connection-style tones only: batch status is not a security severity.
export const STATUS_PRESENTATION: Record<ProcessingStatus, StatusPresentation> = {
  pending: { label: 'Pending', meaning: 'registered, waiting for processing', tone: 'ice' },
  processing: { label: 'Processing', meaning: 'being processed', tone: 'accent' },
  completed: { label: 'Completed', meaning: 'processing finished', tone: 'accent' },
  failed: { label: 'Failed', meaning: 'processing failed', tone: 'muted' },
}

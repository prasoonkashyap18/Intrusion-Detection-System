import { CloudOff } from 'lucide-react'
import { Button } from '../ui/Button'
import { StateBlock } from './StateBlock'

interface OfflineStateProps {
  title?: string
  description?: string
  /** Wired to the caller's health re-check, so this component stays unaware of the API. */
  onRetry?: () => void
  retryLabel?: string
  /** Disables the retry button while a check is already running. */
  isRetrying?: boolean
  compact?: boolean
  className?: string
}

/**
 * Shown when the backend cannot be reached. Distinct from EmptyState on
 * purpose: "nothing to show" and "we could not ask" are different claims, and
 * only one of them is a statement about the data.
 */
export function OfflineState({
  title = 'AI-IDS backend unavailable',
  description = 'The API could not be reached. Check that the FastAPI service is running, then try again.',
  onRetry,
  retryLabel = 'Retry connection',
  isRetrying = false,
  compact,
  className,
}: OfflineStateProps) {
  return (
    <StateBlock
      role="alert"
      icon={CloudOff}
      title={title}
      description={description}
      compact={compact}
      className={className}
      action={
        onRetry && (
          <Button onClick={onRetry} disabled={isRetrying}>
            {isRetrying ? 'Reconnecting…' : retryLabel}
          </Button>
        )
      }
    />
  )
}

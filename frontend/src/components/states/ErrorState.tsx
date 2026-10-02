import { TriangleAlert } from 'lucide-react'
import { Button } from '../ui/Button'
import { StateBlock } from './StateBlock'

interface ErrorStateProps {
  title?: string
  /**
   * User-facing explanation only. Never pass raw exception text, stack traces,
   * URLs or backend internals — the service layer's ApiError codes exist so
   * callers can choose a clean message.
   */
  message: string
  /** Shown as a Retry button when provided. */
  onRetry?: () => void
  retryLabel?: string
  compact?: boolean
  className?: string
}

export function ErrorState({
  title = 'Something went wrong',
  message,
  onRetry,
  retryLabel = 'Retry',
  compact,
  className,
}: ErrorStateProps) {
  return (
    <StateBlock
      role="alert"
      icon={TriangleAlert}
      tone="notice"
      title={title}
      description={message}
      compact={compact}
      className={className}
      action={onRetry && <Button onClick={onRetry}>{retryLabel}</Button>}
    />
  )
}

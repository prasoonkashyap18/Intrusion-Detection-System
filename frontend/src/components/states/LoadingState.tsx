import { usePrefersReducedMotion } from '../../hooks/useMediaQuery'
import { cn } from '../../utils/cn'

interface LoadingStateProps {
  /** Announced to assistive technology. Defaults to a generic message. */
  title?: string
  description?: string
  /** Row layout for tight spaces; the full-area form centres itself in its container. */
  compact?: boolean
  className?: string
}

/**
 * Subtle activity indicator. Uses <output> (implicit role="status") with a
 * polite live region, so the message is announced without interrupting.
 */
export function LoadingState({ title = 'Loading', description, compact = false, className }: LoadingStateProps) {
  return (
    <output
      aria-live="polite"
      aria-busy="true"
      className={cn(
        'flex text-left',
        compact ? 'items-center gap-2.5' : 'flex-col items-center justify-center gap-3 px-6 py-12 text-center',
        className,
      )}
    >
      <Spinner className={compact ? 'size-4' : 'size-5'} />
      <span className={cn('min-w-0', !compact && 'mt-1')}>
        <span className={cn('block font-medium text-graphite-900', compact ? 'text-[13px]' : 'text-sm')}>{title}</span>
        {description && (
          <span
            className={cn(
              'mt-0.5 block text-[13px] leading-relaxed text-graphite-500',
              !compact && 'mx-auto max-w-sm',
            )}
          >
            {description}
          </span>
        )}
      </span>
    </output>
  )
}

/**
 * Thin graphite track with a cyan arc. Users who prefer reduced motion get the
 * static ring instead of a spinning one.
 */
function Spinner({ className }: { className?: string }) {
  const reducedMotion = usePrefersReducedMotion()

  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      data-testid="loading-spinner"
      data-static={reducedMotion ? '' : undefined}
      className={cn('shrink-0', !reducedMotion && 'animate-spin', className)}
    >
      <circle cx="12" cy="12" r="9" className="stroke-graphite-200" strokeWidth="2.5" />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        className="stroke-accent-500"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  )
}

import type { TrafficLevel } from '../../types/security'
import { cn } from '../../utils/cn'

export type StatusTone = 'accent' | 'ice' | 'muted' | TrafficLevel

const TONE_CLASSES: Record<StatusTone, string> = {
  accent: 'bg-accent',
  ice: 'bg-ice',
  muted: 'bg-ink-600',
  normal: 'bg-sev-normal',
  low: 'bg-sev-low',
  medium: 'bg-sev-medium',
  high: 'bg-sev-high',
  critical: 'bg-sev-critical',
}

interface StatusDotProps {
  tone: StatusTone
  /** Adds an expanding signal ring; suppressed automatically for reduced motion. */
  pulse?: boolean
  className?: string
}

export function StatusDot({ tone, pulse = false, className }: StatusDotProps) {
  return (
    <span aria-hidden="true" className={cn('relative inline-flex size-2 shrink-0', className)}>
      {pulse && <span className={cn('absolute inset-0 animate-signal rounded-full', TONE_CLASSES[tone])} />}
      <span className={cn('relative size-2 rounded-full', TONE_CLASSES[tone])} />
    </span>
  )
}

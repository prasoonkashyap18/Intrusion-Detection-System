import type { TrafficLevel } from '../../types/security'
import { cn } from '../../utils/cn'

export type StatusTone = 'accent' | 'ice' | 'muted' | TrafficLevel

const TONE_CLASSES: Record<StatusTone, string> = {
  accent: 'bg-accent-500',
  ice: 'bg-ice-500',
  muted: 'bg-graphite-300',
  normal: 'bg-sev-normal',
  low: 'bg-sev-low',
  medium: 'bg-sev-medium',
  high: 'bg-sev-high',
  critical: 'bg-sev-critical',
}

const HALO_CLASSES: Record<StatusTone, string> = {
  accent: 'ring-accent-100',
  ice: 'ring-ice-100',
  muted: 'ring-graphite-100',
  normal: 'ring-graphite-100',
  low: 'ring-sev-low/15',
  medium: 'ring-sev-medium/15',
  high: 'ring-sev-high/15',
  critical: 'ring-sev-critical/15',
}

interface StatusDotProps {
  tone: StatusTone
  /** Expanding signal ring for in-progress states; suppressed for reduced motion. */
  pulse?: boolean
  /** Soft tinted halo for steady, important states. */
  halo?: boolean
  className?: string
}

export function StatusDot({ tone, pulse = false, halo = false, className }: StatusDotProps) {
  return (
    <span aria-hidden="true" className={cn('relative inline-flex size-2 shrink-0', className)}>
      {pulse && <span className={cn('absolute inset-0 animate-signal rounded-full', TONE_CLASSES[tone])} />}
      <span className={cn('relative size-2 rounded-full', TONE_CLASSES[tone], halo && cn('ring-[3px]', HALO_CLASSES[tone]))} />
    </span>
  )
}

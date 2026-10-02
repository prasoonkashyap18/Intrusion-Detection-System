import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '../../utils/cn'

export type StateTone = 'neutral' | 'notice'

interface StateBlockProps {
  icon: LucideIcon
  title: string
  description?: string
  /** Rendered under the description, e.g. a retry or primary action. */
  action?: ReactNode
  /** `notice` tints the icon for states that need attention. Never the only signal — the title always says what happened. */
  tone?: StateTone
  /** Row layout for tight spaces such as a table cell or a narrow card. */
  compact?: boolean
  /** Set to `alert` so assistive technology announces failures as they appear. */
  role?: 'alert'
  className?: string
}

const ICON_TONES: Record<StateTone, string> = {
  neutral: 'text-graphite-500',
  notice: 'text-accent-700',
}

/**
 * Shared layout for the empty, error and offline states: icon tile, title,
 * optional description and action. Internal to this folder — pages use the
 * named state components so each one's intent stays explicit.
 */
export function StateBlock({
  icon: Icon,
  title,
  description,
  action,
  tone = 'neutral',
  compact = false,
  role,
  className,
}: StateBlockProps) {
  if (compact) {
    return (
      <div role={role} className={cn('flex items-start gap-3 text-left', className)}>
        <span
          className={cn(
            'grid size-8 shrink-0 place-items-center rounded-lg bg-graphite-50 ring-1 ring-graphite-900/6',
            ICON_TONES[tone],
          )}
        >
          <Icon className="size-4" strokeWidth={1.6} aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-graphite-900">{title}</p>
          {description && <p className="mt-0.5 text-[13px] leading-relaxed text-graphite-500">{description}</p>}
          {action && <div className="mt-2.5 flex flex-wrap gap-2">{action}</div>}
        </div>
      </div>
    )
  }

  return (
    <div
      role={role}
      className={cn('flex flex-col items-center justify-center gap-4 px-6 py-12 text-center', className)}
    >
      <span
        className={cn(
          'grid size-12 place-items-center rounded-2xl bg-linear-to-b from-white to-graphite-50 shadow-control ring-1 ring-graphite-900/7',
          ICON_TONES[tone],
        )}
      >
        <Icon className="size-5" strokeWidth={1.6} aria-hidden="true" />
      </span>
      <div>
        <p className="text-sm font-medium text-graphite-900">{title}</p>
        {description && (
          <p className="mx-auto mt-1.5 max-w-sm text-[13px] leading-relaxed text-graphite-500">{description}</p>
        )}
      </div>
      {action && <div className="flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  )
}

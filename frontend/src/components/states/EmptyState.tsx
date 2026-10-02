import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { StateBlock } from './StateBlock'

interface EmptyStateProps {
  icon: LucideIcon
  title: string
  description: string
  /** Optional next step, e.g. an upload action. */
  action?: ReactNode
  compact?: boolean
  className?: string
}

/**
 * Shown when a request succeeded and genuinely returned nothing. It explains
 * the absence rather than filling the space with invented figures.
 */
export function EmptyState({ icon, title, description, action, compact, className }: EmptyStateProps) {
  return (
    <StateBlock
      icon={icon}
      title={title}
      description={description}
      action={action}
      compact={compact}
      className={className}
    />
  )
}

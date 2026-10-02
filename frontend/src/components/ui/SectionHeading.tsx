import type { ReactNode } from 'react'
import { cn } from '../../utils/cn'

interface SectionHeadingProps {
  /** Applied to the heading so the enclosing section can reference it via aria-labelledby. */
  id: string
  title: string
  description?: string
  action?: ReactNode
  className?: string
}

export function SectionHeading({ id, title, description, action, className }: SectionHeadingProps) {
  return (
    <div className={cn('flex flex-wrap items-start justify-between gap-x-6 gap-y-3', className)}>
      <div className="min-w-0 max-w-xl">
        <h2 id={id} className="text-base font-semibold tracking-tight text-graphite-950">
          {title}
        </h2>
        {description && <p className="mt-1 text-sm leading-relaxed text-graphite-500">{description}</p>}
      </div>
      {action}
    </div>
  )
}

import type { ReactNode } from 'react'
import { cn } from '../../utils/cn'

interface SectionHeadingProps {
  /** Applied to the heading so the enclosing section can reference it via aria-labelledby. */
  id: string
  eyebrow: string
  title: string
  action?: ReactNode
  className?: string
}

export function SectionHeading({ id, eyebrow, title, action, className }: SectionHeadingProps) {
  return (
    <div className={cn('flex items-start justify-between gap-4', className)}>
      <div className="min-w-0">
        <p className="font-mono text-micro uppercase tracking-label text-ink-500">{eyebrow}</p>
        <h2 id={id} className="mt-1.5 text-[15px] font-semibold tracking-tight text-ink-50">
          {title}
        </h2>
      </div>
      {action}
    </div>
  )
}

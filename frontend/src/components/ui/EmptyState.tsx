import type { LucideIcon } from 'lucide-react'
import { cn } from '../../utils/cn'

interface EmptyStateProps {
  icon: LucideIcon
  title: string
  description: string
  className?: string
}

export function EmptyState({ icon: Icon, title, description, className }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-4 px-6 py-10 text-center', className)}>
      <span className="grid size-11 place-items-center rounded-xl border border-white/8 bg-white/3 text-ink-400">
        <Icon className="size-5" strokeWidth={1.5} aria-hidden="true" />
      </span>
      <div>
        <p className="text-sm font-medium text-ink-100">{title}</p>
        <p className="mx-auto mt-1.5 max-w-xs text-xs leading-relaxed text-ink-500">{description}</p>
      </div>
    </div>
  )
}

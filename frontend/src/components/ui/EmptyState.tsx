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
    <div className={cn('flex flex-col items-center justify-center gap-4 px-6 py-12 text-center', className)}>
      <span className="grid size-12 place-items-center rounded-2xl bg-linear-to-b from-white to-graphite-50 text-graphite-500 shadow-control ring-1 ring-graphite-900/7">
        <Icon className="size-5" strokeWidth={1.6} aria-hidden="true" />
      </span>
      <div>
        <p className="text-sm font-medium text-graphite-900">{title}</p>
        <p className="mx-auto mt-1.5 max-w-sm text-[13px] leading-relaxed text-graphite-500">{description}</p>
      </div>
    </div>
  )
}

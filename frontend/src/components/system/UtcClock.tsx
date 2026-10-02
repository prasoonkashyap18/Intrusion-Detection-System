import { useNow } from '../../hooks/useNow'
import { cn } from '../../utils/cn'
import { formatUtcTime } from '../../utils/format'

export function UtcClock({ className }: { className?: string }) {
  const now = useNow()

  return (
    <time
      dateTime={now.toISOString()}
      className={cn('inline-flex items-baseline gap-1.5 font-mono text-xs tabular-nums text-ink-300', className)}
    >
      {formatUtcTime(now)}
      <span className="text-ink-500">UTC</span>
    </time>
  )
}

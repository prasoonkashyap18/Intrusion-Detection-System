import { useNow } from '../../hooks/useNow'
import { formatUtcTime } from '../../utils/format'

export function UtcClock() {
  const now = useNow()

  return (
    <time
      dateTime={now.toISOString()}
      className="inline-flex items-baseline gap-1.5 font-mono text-xs tabular-nums text-graphite-700"
    >
      {formatUtcTime(now)}
      <span className="text-graphite-500">UTC</span>
    </time>
  )
}

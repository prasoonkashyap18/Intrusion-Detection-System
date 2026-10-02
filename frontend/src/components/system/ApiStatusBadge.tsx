import type { ApiHealth } from '../../hooks/useApiHealth'
import { StatusDot } from '../ui/StatusDot'
import { API_STATUS_PRESENTATION } from './apiStatusPresentation'

export function ApiStatusBadge({ health }: { health: ApiHealth }) {
  const presentation = API_STATUS_PRESENTATION[health.status]

  return (
    <output
      aria-live="polite"
      className="inline-flex items-center gap-2 rounded-full border border-white/8 bg-white/3 px-3 py-1.5 text-xs font-medium text-ink-200"
    >
      <StatusDot tone={presentation.tone} pulse={presentation.pulse} />
      <span>
        <span className="hidden sm:inline">API </span>
        {presentation.label}
      </span>
      {health.status === 'online' && health.latencyMs !== null && (
        <span className="hidden font-mono tabular-nums text-ink-500 sm:inline">{health.latencyMs} ms</span>
      )}
    </output>
  )
}

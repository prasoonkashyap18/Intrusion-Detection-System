import type { ApiHealth } from '../../hooks/useApiHealth'
import { StatusDot } from '../ui/StatusDot'
import { API_STATUS_PRESENTATION } from './apiStatusPresentation'

export function ApiStatusBadge({ health }: { health: ApiHealth }) {
  const presentation = API_STATUS_PRESENTATION[health.status]

  return (
    <output
      aria-live="polite"
      className="inline-flex items-center gap-2 rounded-full bg-white/70 px-2.5 py-1 text-[13px] text-graphite-600 ring-1 ring-graphite-900/6"
    >
      <span className="hidden text-graphite-500 sm:inline">API</span>
      <StatusDot tone={presentation.tone} pulse={presentation.pulse} halo={health.status === 'online'} />
      <span className="font-medium text-graphite-900">{presentation.label}</span>
      {health.status === 'online' && health.latencyMs !== null && (
        <span className="hidden font-mono text-xs tabular-nums text-graphite-500 sm:inline">{health.latencyMs} ms</span>
      )}
    </output>
  )
}

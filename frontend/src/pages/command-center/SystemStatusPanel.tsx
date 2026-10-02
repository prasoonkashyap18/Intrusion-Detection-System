import { Cpu, Database, RefreshCw, Server, type LucideIcon } from 'lucide-react'
import { API_STATUS_PRESENTATION } from '../../components/system/apiStatusPresentation'
import { NoData } from '../../components/ui/NoData'
import { Panel } from '../../components/ui/Panel'
import { SectionHeading } from '../../components/ui/SectionHeading'
import { StatusDot, type StatusTone } from '../../components/ui/StatusDot'
import type { ApiHealth } from '../../hooks/useApiHealth'
import { API_BASE_URL } from '../../services/config'
import { cn } from '../../utils/cn'
import { formatHost, formatUtcTime } from '../../utils/format'

const API_HOST = formatHost(API_BASE_URL)

function describeApi(health: ApiHealth): string {
  switch (health.status) {
    case 'checking':
      return `Contacting ${API_HOST}`
    case 'online':
      return health.latencyMs === null ? API_HOST : `${API_HOST} · ${health.latencyMs} ms round trip`
    case 'degraded':
      return 'Reachable, but not reporting healthy'
    case 'offline':
      return `Unreachable at ${API_HOST}`
  }
}

export function SystemStatusPanel({ apiHealth }: { apiHealth: ApiHealth }) {
  const api = API_STATUS_PRESENTATION[apiHealth.status]
  const isChecking = apiHealth.status === 'checking'

  return (
    <Panel interaction="spotlight" aria-labelledby="platform-status-heading" className="p-5">
      <SectionHeading
        id="platform-status-heading"
        eyebrow="System"
        title="Platform status"
        action={
          <button
            type="button"
            onClick={apiHealth.recheck}
            disabled={isChecking}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/8 bg-white/3 px-2.5 py-1.5 text-xs font-medium text-ink-200 transition-colors hover:border-accent/40 hover:text-ink-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw className={cn('size-3.5', isChecking && 'animate-spin')} aria-hidden="true" />
            Re-check
          </button>
        }
      />

      <ul className="mt-4 divide-y divide-white/6">
        <StatusRow icon={Server} label="Backend API" value={api.label} tone={api.tone} detail={describeApi(apiHealth)} />
        <StatusRow
          icon={Cpu}
          label="Detection engine"
          value="Not integrated"
          tone="muted"
          detail="No detection API is available yet"
        />
        <StatusRow
          icon={Database}
          label="Data store"
          value="Not reported"
          tone="muted"
          detail="The health check covers the API process only"
        />
      </ul>

      <p className="mt-4 flex items-center gap-2 font-mono text-micro text-ink-500">
        <span className="uppercase tracking-label">Last check</span>
        <span className="tabular-nums text-ink-300">
          {apiHealth.lastCheckedAt ? `${formatUtcTime(apiHealth.lastCheckedAt)} UTC` : <NoData />}
        </span>
      </p>
    </Panel>
  )
}

interface StatusRowProps {
  icon: LucideIcon
  label: string
  value: string
  tone: StatusTone
  detail: string
}

function StatusRow({ icon: Icon, label, value, tone, detail }: StatusRowProps) {
  return (
    <li className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
      <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg border border-white/8 bg-white/3 text-ink-400">
        <Icon className="size-4" strokeWidth={1.75} aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-ink-200">{label}</span>
          <span className="flex shrink-0 items-center gap-2 text-xs font-medium text-ink-100">
            <StatusDot tone={tone} />
            {value}
          </span>
        </div>
        <p className="mt-0.5 truncate text-xs text-ink-500">{detail}</p>
      </div>
    </li>
  )
}

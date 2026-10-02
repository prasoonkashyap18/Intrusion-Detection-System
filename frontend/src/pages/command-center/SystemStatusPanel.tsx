import { Cpu, Database, RefreshCw, Server, type LucideIcon } from 'lucide-react'
import { API_STATUS_PRESENTATION } from '../../components/system/apiStatusPresentation'
import { Button } from '../../components/ui/Button'
import { NoData } from '../../components/ui/NoData'
import { Panel } from '../../components/ui/Panel'
import { SectionHeading } from '../../components/ui/SectionHeading'
import { StatusDot, type StatusTone } from '../../components/ui/StatusDot'
import type { ApiHealth } from '../../hooks/useApiHealth'
import { API_BASE_URL } from '../../services'
import { cn } from '../../utils/cn'
import { formatHost, formatUtcTime } from '../../utils/format'

const API_HOST = formatHost(API_BASE_URL)

function describeApi(health: ApiHealth): string {
  switch (health.status) {
    case 'connecting':
      return `Contacting ${API_HOST}`
    case 'online':
      return health.latencyMs === null ? API_HOST : `${API_HOST} · ${health.latencyMs} ms round trip`
    case 'degraded':
      return 'Reachable, but not reporting healthy'
    case 'offline':
      return `Unreachable at ${API_HOST}`
  }
}

export function SystemStatusPanel({ apiHealth, className }: { apiHealth: ApiHealth; className?: string }) {
  const api = API_STATUS_PRESENTATION[apiHealth.status]
  const isConnecting = apiHealth.status === 'connecting'

  return (
    <Panel interaction="spotlight" aria-labelledby="platform-status-heading" className={cn('p-6 lg:p-7', className)}>
      <SectionHeading
        id="platform-status-heading"
        title="Platform status"
        description="Live connectivity from the backend health check."
        action={
          <Button magnetic onClick={apiHealth.recheck} disabled={isConnecting}>
            <RefreshCw className={cn('size-3.5', isConnecting && 'animate-spin')} aria-hidden="true" />
            Re-check
          </Button>
        }
      />

      <ul className="mt-6 divide-y divide-graphite-900/6">
        <StatusRow
          icon={Server}
          label="Backend API"
          value={api.label}
          tone={api.tone}
          halo={apiHealth.status === 'online'}
          detail={describeApi(apiHealth)}
        />
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

      <p className="mt-5 flex items-center gap-2 text-xs text-graphite-500">
        Last check
        <span className="font-mono tabular-nums text-graphite-700">
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
  halo?: boolean
  detail: string
}

function StatusRow({ icon: Icon, label, value, tone, halo = false, detail }: StatusRowProps) {
  return (
    <li className="flex items-start gap-3.5 py-3.5 first:pt-0 last:pb-0">
      <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-[10px] bg-graphite-50 text-graphite-600 ring-1 ring-graphite-900/5">
        <Icon className="size-4" strokeWidth={1.6} aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm font-medium text-graphite-900">{label}</span>
          <span className="flex shrink-0 items-center gap-2 text-[13px] text-graphite-700">
            <StatusDot tone={tone} halo={halo} />
            {value}
          </span>
        </div>
        <p className="mt-0.5 truncate text-[13px] text-graphite-500">{detail}</p>
      </div>
    </li>
  )
}

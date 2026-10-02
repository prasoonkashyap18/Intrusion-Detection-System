import type { ApiConnectionStatus } from '../../types/api'
import type { StatusTone } from '../ui/StatusDot'

interface ApiStatusPresentation {
  label: string
  tone: StatusTone
  pulse: boolean
}

// Connectivity is not a security severity, so it never uses severity colors.
export const API_STATUS_PRESENTATION: Record<ApiConnectionStatus, ApiStatusPresentation> = {
  checking: { label: 'Checking', tone: 'ice', pulse: true },
  online: { label: 'Online', tone: 'accent', pulse: true },
  degraded: { label: 'Degraded', tone: 'ice', pulse: false },
  offline: { label: 'Offline', tone: 'muted', pulse: false },
}

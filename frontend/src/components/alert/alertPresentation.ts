import type { SeverityLevel } from '../../types/security'
import type { AlertLifecycleStatus } from '../../types/alert'
import type { StatusTone } from '../ui/StatusDot'

export interface SeverityPresentation {
  label: string
  tone: StatusTone
}

// Reuses the existing sev-* tone palette (StatusDot) — no new colors
// invented for alerts.
export const SEVERITY_PRESENTATION: Record<SeverityLevel, SeverityPresentation> = {
  low: { label: 'Low', tone: 'low' },
  medium: { label: 'Medium', tone: 'medium' },
  high: { label: 'High', tone: 'high' },
  critical: { label: 'Critical', tone: 'critical' },
}

export interface AlertStatusPresentation {
  label: string
  tone: StatusTone
}

export const ALERT_STATUS_PRESENTATION: Record<AlertLifecycleStatus, AlertStatusPresentation> = {
  open: { label: 'Open', tone: 'critical' },
  acknowledged: { label: 'Acknowledged', tone: 'medium' },
  investigating: { label: 'Investigating', tone: 'accent' },
  resolved: { label: 'Resolved', tone: 'normal' },
  false_positive: { label: 'False positive', tone: 'muted' },
}

export const ALERT_STATUS_OPTIONS: { value: AlertLifecycleStatus; label: string }[] = [
  { value: 'open', label: 'Open' },
  { value: 'acknowledged', label: 'Acknowledged' },
  { value: 'investigating', label: 'Investigating' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'false_positive', label: 'False positive' },
]

export const HYBRID_OUTCOME_LABELS: Record<string, string> = {
  confirmed_attack: 'Confirmed attack (both signals agree)',
  supervised_attack_only: 'Supervised classifier flagged attack',
  anomaly_only: 'Anomaly detector flagged this row',
  clean: 'Clean',
}

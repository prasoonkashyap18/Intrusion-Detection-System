import { X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ALERT_STATUS_OPTIONS, ALERT_STATUS_PRESENTATION, HYBRID_OUTCOME_LABELS, SEVERITY_PRESENTATION } from '../../components/alert/alertPresentation'
import { ErrorState, LoadingState } from '../../components/states'
import { Button } from '../../components/ui/Button'
import { NoData } from '../../components/ui/NoData'
import { StatusDot } from '../../components/ui/StatusDot'
import { useAuth } from '../../contexts/authContextBase'
import { getAlert, getAlertHistory, isApiError, updateAlertStatus } from '../../services'
import type { Alert, AlertLifecycleStatus, AlertStatusHistoryEntry } from '../../types/alert'
import { formatLocalDateTime, formatPercent, formatUtcDateTime } from '../../utils/format'

interface AlertDetailDialogProps {
  alertId: string
  onClose: () => void
  onStatusChanged?: () => void
}

/**
 * Investigation view for one alert: the hybrid outcome, risk score,
 * contributing signals, and the structured explanation
 * (measured values / rule-based statements / unavailable information)
 * already computed and persisted by the backend (Steps 33-36) — this
 * component never recomputes or guesses any of it, only displays what
 * `Alert.evidence` already contains.
 */
export function AlertDetailDialog({ alertId, onClose, onStatusChanged }: AlertDetailDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const { hasRole } = useAuth()
  const canUpdateStatus = hasRole('administrator', 'security_analyst')

  const [alert, setAlert] = useState<Alert | null>(null)
  const [history, setHistory] = useState<AlertStatusHistoryEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [isUpdating, setIsUpdating] = useState(false)

  const load = useCallback((signal?: AbortSignal) => {
    Promise.all([getAlert(alertId, signal), getAlertHistory(alertId, signal)])
      .then(([alertResult, historyResult]) => {
        setAlert(alertResult)
        setHistory(historyResult.items)
        setError(null)
      })
      .catch((loadError: unknown) => {
        if (isApiError(loadError) && loadError.code === 'aborted') return
        setError('Unable to load this alert.')
      })
  }, [alertId])

  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
    return () => controller.abort()
  }, [load])

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return undefined
    dialog.addEventListener('close', onClose)
    if (!dialog.open) dialog.showModal()
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      dialog.removeEventListener('close', onClose)
      document.body.style.overflow = previousOverflow
    }
  }, [onClose])

  const closeDialog = useCallback(() => dialogRef.current?.close(), [])

  async function handleStatusChange(status: AlertLifecycleStatus) {
    setIsUpdating(true)
    setError(null)
    try {
      await updateAlertStatus(alertId, status)
      load()
      onStatusChanged?.()
    } catch {
      setError('Unable to update the alert status. You may not have permission, or the transition is not valid.')
    } finally {
      setIsUpdating(false)
    }
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="alert-detail-heading"
      closedby="any"
      className="fixed top-1/2 left-1/2 m-0 max-h-[85vh] w-[min(720px,92vw)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-graphite-900/8 bg-white p-0 text-graphite-800 shadow-raised backdrop:bg-graphite-950/30 backdrop:backdrop-blur-sm"
    >
      <div className="p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="font-mono text-micro uppercase tracking-label text-graphite-500">Alert investigation</p>
            <h2 id="alert-detail-heading" className="mt-1 truncate text-lg font-semibold text-graphite-950">
              {alert?.title ?? 'Alert detail'}
            </h2>
          </div>
          <Button variant="ghost" size="icon" aria-label="Close" onClick={closeDialog}>
            <X className="size-4" aria-hidden="true" />
          </Button>
        </div>

        <div className="mt-6">
          {alert ? (
            <AlertDetailBody
              alert={alert}
              history={history}
              canUpdateStatus={canUpdateStatus}
              isUpdating={isUpdating}
              error={error}
              onStatusChange={handleStatusChange}
            />
          ) : error ? (
            <ErrorState title="Unable to load this alert" message={error} onRetry={() => load()} />
          ) : (
            <LoadingState title="Loading alert detail" description="Contacting the AI-IDS API." />
          )}
        </div>
      </div>
    </dialog>
  )
}

interface BodyProps {
  alert: Alert
  history: AlertStatusHistoryEntry[]
  canUpdateStatus: boolean
  isUpdating: boolean
  error: string | null
  onStatusChange: (status: AlertLifecycleStatus) => void
}

function AlertDetailBody({ alert, history, canUpdateStatus, isUpdating, error, onStatusChange }: BodyProps) {
  const severity = SEVERITY_PRESENTATION[alert.severity]
  const status = ALERT_STATUS_PRESENTATION[alert.status]
  const evidence = alert.evidence
  const measured = evidence.explanation?.measured_values

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <span className="inline-flex items-center gap-2 rounded-full bg-graphite-900/5 px-2.5 py-1 text-xs font-medium text-graphite-800">
          <StatusDot tone={severity.tone} halo /> {severity.label} severity
        </span>
        <span className="inline-flex items-center gap-2 rounded-full bg-graphite-900/5 px-2.5 py-1 text-xs font-medium text-graphite-800">
          <StatusDot tone={status.tone} /> {status.label}
        </span>
        <span className="text-xs text-graphite-500">Row {alert.row_number} · Batch {alert.batch_id.slice(0, 8)}</span>
      </div>

      <p className="text-sm text-graphite-700">{alert.description}</p>

      <section aria-labelledby="hybrid-outcome-heading">
        <h3 id="hybrid-outcome-heading" className="text-xs font-semibold tracking-label text-graphite-500 uppercase">
          Hybrid detection outcome
        </h3>
        <p className="mt-1 text-sm text-graphite-800">
          {HYBRID_OUTCOME_LABELS[evidence.hybrid_outcome] ?? evidence.hybrid_outcome}
        </p>
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {evidence.contributing_signals.map((signal) => (
            <li key={signal} className="rounded-full bg-graphite-900/5 px-2 py-0.5 text-[11px] font-mono text-graphite-600">
              {signal}
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="risk-heading" className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <h3 id="risk-heading" className="sr-only">
          Risk and model signals
        </h3>
        <Metric label="Risk score" value={`${evidence.risk_score.toFixed(1)} / 100`} />
        <Metric
          label="Attack probability"
          value={
            measured?.attack_probability === null || measured?.attack_probability === undefined ? (
              <NoData />
            ) : (
              formatPercent(measured.attack_probability)
            )
          }
        />
        <Metric
          label="Anomaly score"
          value={measured?.anomaly_score === null || measured?.anomaly_score === undefined ? <NoData /> : measured.anomaly_score.toFixed(3)}
        />
      </section>

      {evidence.explanation && (
        <section aria-labelledby="explanation-heading">
          <h3 id="explanation-heading" className="text-xs font-semibold tracking-label text-graphite-500 uppercase">
            Explanation
          </h3>
          {evidence.explanation.rule_based_explanations.length > 0 && (
            <ul className="mt-2 flex flex-col gap-1.5 text-[13px] text-graphite-700">
              {evidence.explanation.rule_based_explanations.map((text, index) => (
                <li key={index} className="flex gap-2">
                  <span aria-hidden="true" className="text-graphite-400">
                    •
                  </span>
                  {text}
                </li>
              ))}
            </ul>
          )}
          {evidence.explanation.unavailable_information.length > 0 && (
            <ul className="mt-3 flex flex-col gap-1 text-[13px] text-graphite-500 italic">
              {evidence.explanation.unavailable_information.map((text, index) => (
                <li key={index}>{text}</li>
              ))}
            </ul>
          )}
          {evidence.explanation.feature_importance && evidence.explanation.feature_importance.length > 0 && (
            <div className="mt-3">
              <p className="text-[13px] text-graphite-500">
                Global feature importance (model-wide — not specific to this row):
              </p>
              <ul className="mt-1.5 flex flex-wrap gap-1.5">
                {evidence.explanation.feature_importance
                  .slice()
                  .sort((a, b) => b.importance - a.importance)
                  .slice(0, 6)
                  .map((item) => (
                    <li key={item.feature_name} className="rounded-full bg-graphite-900/5 px-2 py-0.5 text-[11px] text-graphite-600">
                      {item.feature_name}: {formatPercent(item.importance)}
                    </li>
                  ))}
              </ul>
            </div>
          )}
        </section>
      )}

      <section aria-labelledby="lifecycle-heading">
        <h3 id="lifecycle-heading" className="text-xs font-semibold tracking-label text-graphite-500 uppercase">
          Lifecycle
        </h3>
        {canUpdateStatus ? (
          <div className="mt-2 flex flex-wrap gap-2">
            {ALERT_STATUS_OPTIONS.map((option) => (
              <Button
                key={option.value}
                variant={alert.status === option.value ? 'primary' : 'secondary'}
                disabled={isUpdating}
                onClick={() => onStatusChange(option.value)}
              >
                {option.label}
              </Button>
            ))}
          </div>
        ) : (
          <p className="mt-2 text-[13px] text-graphite-500">
            Your role can view this alert's status but cannot change it.
          </p>
        )}
        {error && (
          <p role="alert" className="mt-2 text-[13px] text-red-600">
            {error}
          </p>
        )}

        {history.length === 0 ? (
          <p className="mt-3 text-[13px] text-graphite-500">No status changes yet — this alert is still in its initial state.</p>
        ) : (
          <ol className="mt-4 flex flex-col gap-2 border-t border-graphite-900/6 pt-3">
            {history.map((entry) => (
              <li key={entry.id} className="text-[13px] text-graphite-600">
                <span className="text-graphite-800">
                  {ALERT_STATUS_PRESENTATION[entry.previous_status].label} → {ALERT_STATUS_PRESENTATION[entry.new_status].label}
                </span>{' '}
                <time dateTime={entry.changed_at} title={formatUtcDateTime(entry.changed_at)} className="tabular-nums text-graphite-400">
                  {formatLocalDateTime(entry.changed_at)}
                </time>
                {entry.note && <span className="block text-graphite-500">“{entry.note}”</span>}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-xl bg-graphite-900/4 px-3 py-2.5">
      <p className="text-[11px] text-graphite-500">{label}</p>
      <p className="mt-0.5 text-sm font-medium tabular-nums text-graphite-900">{value}</p>
    </div>
  )
}

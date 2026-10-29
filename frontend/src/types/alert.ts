import type { SeverityLevel } from './security'

/** Mirrors the backend `AlertStatus` enum. */
export type AlertLifecycleStatus = 'open' | 'acknowledged' | 'investigating' | 'resolved' | 'false_positive'

/** Mirrors the backend `HybridOutcome` enum (app.services.hybrid_detection). */
export type HybridOutcome = 'confirmed_attack' | 'supervised_attack_only' | 'anomaly_only' | 'clean'

/** The structured explanation embedded in `Alert.evidence.explanation` —
 * see app.services.explainability.Explanation.as_dict(). Every field is
 * either a real measured value, a genuine rule-based statement, or an
 * explicit "unavailable" marker; nothing here is ever fabricated. */
export interface AlertExplanation {
  measured_values: {
    supervised_available?: boolean
    predicted_label?: number | null
    prediction_name?: string | null
    attack_probability?: number | null
    anomaly_available?: boolean
    is_anomaly?: boolean | null
    anomaly_score?: number | null
    hybrid_outcome?: HybridOutcome
    risk_score?: number
    severity?: SeverityLevel
  }
  rule_based_explanations: string[]
  unavailable_information: string[]
  feature_importance: { feature_name: string; importance: number }[] | null
  feature_importance_scope: string | null
}

/** The structured, never-fabricated snapshot `Alert.evidence` holds —
 * see app.services.alert_generation.build_alert_draft. */
export interface AlertEvidence {
  hybrid_outcome: HybridOutcome
  contributing_signals: string[]
  risk_score: number
  severity: SeverityLevel
  explanation: AlertExplanation
}

export interface Alert {
  id: string
  batch_id: string
  row_number: number
  detection_result_id: string | null
  anomaly_result_id: string | null
  hybrid_result_id: string | null
  risk_assessment_id: string | null
  explanation_id: string | null
  title: string
  description: string
  severity: SeverityLevel
  status: AlertLifecycleStatus
  evidence: AlertEvidence
  created_at: string
  updated_at: string
}

export interface AlertListResponse {
  items: Alert[]
  page: number
  page_size: number
  total_items: number
  total_pages: number
}

export interface AlertStatusHistoryEntry {
  id: string
  previous_status: AlertLifecycleStatus
  new_status: AlertLifecycleStatus
  note: string | null
  changed_at: string
}

export interface AlertStatusHistoryResponse {
  alert_id: string
  items: AlertStatusHistoryEntry[]
}

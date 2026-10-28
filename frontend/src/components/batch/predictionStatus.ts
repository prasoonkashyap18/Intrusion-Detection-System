import type { PredictionName } from '../../types/detection'
import type { StatusTone } from '../ui/StatusDot'

export interface PredictionPresentation {
  label: string
  /** What the prediction means, in words, so it never depends on colour alone. */
  meaning: string
  tone: StatusTone
}

// Reuses the existing severity-tone palette: benign maps to the same
// "normal" tone used for non-malicious traffic elsewhere, attack to
// "critical" — the strongest existing tone, not a new colour invented for
// this feature. `meaning` is deliberately phrased as a model prediction,
// never a certainty, per the project's no-fabricated-confidence stance.
export const PREDICTION_PRESENTATION: Record<PredictionName, PredictionPresentation> = {
  benign: { label: 'Benign', meaning: 'model predicts non-malicious', tone: 'normal' },
  attack: { label: 'Attack', meaning: 'model predicts malicious', tone: 'critical' },
}

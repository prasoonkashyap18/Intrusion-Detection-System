/** Severity levels assigned to detection results (mirrors the backend `Severity` enum). */
export type SeverityLevel = 'low' | 'medium' | 'high' | 'critical'

/** Severity levels plus the neutral tone used for benign ("normal") traffic. */
export type TrafficLevel = 'normal' | SeverityLevel

/** Lifecycle of a detection batch (mirrors the backend `ProcessingStatus` enum). */
export type ProcessingStatus = 'pending' | 'processing' | 'completed' | 'failed'

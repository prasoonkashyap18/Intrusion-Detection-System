/** Mirrors the backend `CaptureSessionStatus` enum. */
export type CaptureSessionStatus = 'running' | 'stopped' | 'failed'

export interface CaptureSessionStats {
  packets_received?: number
  packets_processed?: number
  packets_dropped?: number
  flows_created?: number
  flows_finalized?: number
  parse_failures?: number
  [key: string]: number | undefined
}

export interface CaptureSession {
  id: string
  batch_id: string
  interface_name: string
  bpf_filter: string | null
  idle_timeout_seconds: number
  status: CaptureSessionStatus
  error_message: string | null
  stats: CaptureSessionStats
  started_at: string
  stopped_at: string | null
}

export interface CaptureSessionListResponse {
  items: CaptureSession[]
}

export interface InterfaceListResponse {
  interfaces: string[]
}

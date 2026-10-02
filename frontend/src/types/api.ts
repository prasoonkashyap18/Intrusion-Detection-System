/** Response body of the backend's `GET /api/v1/health` endpoint. */
export interface ApiHealthResponse {
  status: string
  service: string
}

/** Frontend view of backend reachability, derived only from the health endpoint. */
export type ApiConnectionStatus = 'connecting' | 'online' | 'degraded' | 'offline'

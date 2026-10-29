/** Mirrors the backend `UserRole` enum. */
export type UserRole = 'administrator' | 'security_analyst' | 'read_only_viewer'

/** Response body of GET /api/v1/auth/me and the `user` field of a login response. */
export interface AuthUser {
  id: string
  username: string
  role: UserRole
  is_active: boolean
  created_at: string
  last_login_at: string | null
}

/** Response body of POST /api/v1/auth/login. */
export interface LoginResponse {
  access_token: string
  token_type: string
  user: AuthUser
}

import type { AuthUser, LoginResponse, UserRole } from '../types/auth'
import { isVoid, request } from './api'
import { API_V1_PREFIX } from './config'

const ROLES: readonly UserRole[] = ['administrator', 'security_analyst', 'read_only_viewer']

function isAuthUser(value: unknown): value is AuthUser {
  return (
    typeof value === 'object' &&
    value !== null &&
    'id' in value &&
    typeof value.id === 'string' &&
    'username' in value &&
    typeof value.username === 'string' &&
    'role' in value &&
    typeof value.role === 'string' &&
    (ROLES as readonly string[]).includes(value.role) &&
    'is_active' in value &&
    typeof value.is_active === 'boolean' &&
    'created_at' in value &&
    typeof value.created_at === 'string' &&
    'last_login_at' in value &&
    (value.last_login_at === null || typeof value.last_login_at === 'string')
  )
}

function isLoginResponse(value: unknown): value is LoginResponse {
  return (
    typeof value === 'object' &&
    value !== null &&
    'access_token' in value &&
    typeof value.access_token === 'string' &&
    'token_type' in value &&
    typeof value.token_type === 'string' &&
    'user' in value &&
    isAuthUser(value.user)
  )
}

/** POST /api/v1/auth/login */
export function login(username: string, password: string, signal?: AbortSignal): Promise<LoginResponse> {
  return request(`${API_V1_PREFIX}/auth/login`, {
    method: 'POST',
    body: { username, password },
    validate: isLoginResponse,
    signal,
  })
}

/** POST /api/v1/auth/logout — revokes the current session server-side. */
export function logout(signal?: AbortSignal): Promise<void> {
  return request(`${API_V1_PREFIX}/auth/logout`, { method: 'POST', validate: isVoid, signal })
}

/** GET /api/v1/auth/me — the authenticated user's own profile. */
export function getCurrentUser(signal?: AbortSignal): Promise<AuthUser> {
  return request(`${API_V1_PREFIX}/auth/me`, { validate: isAuthUser, signal })
}

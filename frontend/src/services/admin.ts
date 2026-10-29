import type { AuthUser, UserRole } from '../types/auth'
import { request } from './api'
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
    typeof value.is_active === 'boolean'
  )
}

function isUserListResponse(value: unknown): value is { items: AuthUser[] } {
  return (
    typeof value === 'object' && value !== null && 'items' in value && Array.isArray(value.items) && value.items.every(isAuthUser)
  )
}

/** GET /api/v1/admin/users — administrator only. */
export function getUsers(signal?: AbortSignal): Promise<{ items: AuthUser[] }> {
  return request(`${API_V1_PREFIX}/admin/users`, { validate: isUserListResponse, signal })
}

/** POST /api/v1/admin/users — administrator only; the only way a new account is created. */
export function createUser(username: string, password: string, role: UserRole, signal?: AbortSignal): Promise<AuthUser> {
  return request(`${API_V1_PREFIX}/admin/users`, {
    method: 'POST',
    body: { username, password, role },
    validate: isAuthUser,
    signal,
  })
}

/** PATCH /api/v1/admin/users/{userId} — administrator only. */
export function updateUser(
  userId: string,
  changes: { role?: UserRole; is_active?: boolean },
  signal?: AbortSignal,
): Promise<AuthUser> {
  return request(`${API_V1_PREFIX}/admin/users/${encodeURIComponent(userId)}`, {
    method: 'PATCH',
    body: changes,
    validate: isAuthUser,
    signal,
  })
}

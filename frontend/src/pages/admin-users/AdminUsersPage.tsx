import { UserPlus } from 'lucide-react'
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react'
import { ErrorState, LoadingState } from '../../components/states'
import { Button } from '../../components/ui/Button'
import { Panel } from '../../components/ui/Panel'
import { SectionHeading } from '../../components/ui/SectionHeading'
import { StatusDot } from '../../components/ui/StatusDot'
import { createUser, getUsers, isApiError, updateUser } from '../../services'
import type { AuthUser, UserRole } from '../../types/auth'
import { formatLocalDateTime } from '../../utils/format'

const ROLE_OPTIONS: { value: UserRole; label: string }[] = [
  { value: 'read_only_viewer', label: 'Read-Only Viewer' },
  { value: 'security_analyst', label: 'Security Analyst' },
  { value: 'administrator', label: 'Administrator' },
]

/**
 * Administrator-only user management (Step 46): the only frontend
 * surface that creates an account or changes a role/active status —
 * backed entirely by the admin-only `POST/GET/PATCH /api/v1/admin/users`
 * endpoints. This page is reachable only via a route already gated to
 * `administrator` (see App.tsx's RequireAuth), but every one of its
 * requests is independently re-checked by the backend regardless.
 */
export function AdminUsersPage() {
  const [users, setUsers] = useState<AuthUser[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback((signal?: AbortSignal) => {
    getUsers(signal)
      .then((result) => setUsers(result.items))
      .catch((loadError: unknown) => {
        if (isApiError(loadError) && loadError.code === 'aborted') return
        setError('Unable to load users. You may not have administrator access.')
      })
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
    return () => controller.abort()
  }, [load])

  async function handleRoleChange(user: AuthUser, role: UserRole) {
    try {
      await updateUser(user.id, { role })
      load()
    } catch {
      setError(`Could not change ${user.username}'s role.`)
    }
  }

  async function handleToggleActive(user: AuthUser) {
    try {
      await updateUser(user.id, { is_active: !user.is_active })
      load()
    } catch {
      setError(`Could not update ${user.username}'s active status.`)
    }
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-8 px-5 py-8 md:px-8 lg:px-12">
      <header>
        <p className="font-mono text-micro uppercase tracking-label text-graphite-500">Administration</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-graphite-950">User Management</h1>
      </header>

      <Panel className="p-5">
        <SectionHeading id="create-user-heading" title="Create a new account" description="There is no public registration — this is the only way a new account is created." />
        <div className="mt-4">
          <CreateUserForm onCreated={load} />
        </div>
      </Panel>

      <Panel className="p-5">
        <SectionHeading id="users-heading" title="Existing accounts" />
        <div className="mt-4">
          {error && (
            <p role="alert" className="mb-3 text-sm text-red-600">
              {error}
            </p>
          )}
          {users ? (
            <UsersList users={users} onRoleChange={handleRoleChange} onToggleActive={handleToggleActive} />
          ) : error ? (
            <ErrorState compact title="Unable to load users" message={error} />
          ) : (
            <LoadingState compact title="Loading users" />
          )}
        </div>
      </Panel>
    </div>
  )
}

function CreateUserForm({ onCreated }: { onCreated: () => void }) {
  const usernameId = useId()
  const passwordId = useId()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<UserRole>('read_only_viewer')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setIsSubmitting(true)
    try {
      await createUser(username, password, role)
      setUsername('')
      setPassword('')
      setRole('read_only_viewer')
      onCreated()
    } catch (submitError) {
      setError(
        isApiError(submitError) && submitError.status === 409
          ? 'That username is already in use.'
          : 'Could not create the account.',
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
      <div className="flex flex-col gap-1">
        <label htmlFor={usernameId} className="text-xs text-graphite-600">
          Username
        </label>
        <input
          id={usernameId}
          required
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          className="h-9 w-40 rounded-lg border border-graphite-900/10 px-2.5 text-sm outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/20"
        />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={passwordId} className="text-xs text-graphite-600">
          Temporary password
        </label>
        <input
          id={passwordId}
          type="password"
          required
          minLength={8}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="h-9 w-40 rounded-lg border border-graphite-900/10 px-2.5 text-sm outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/20"
        />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-graphite-600" htmlFor="new-user-role">
          Role
        </label>
        <select
          id="new-user-role"
          value={role}
          onChange={(event) => setRole(event.target.value as UserRole)}
          className="h-9 rounded-lg border border-graphite-900/10 px-2 text-sm"
        >
          {ROLE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      <Button type="submit" variant="primary" disabled={isSubmitting}>
        <UserPlus className="size-3.5" aria-hidden="true" />
        {isSubmitting ? 'Creating…' : 'Create user'}
      </Button>
      {error && (
        <p role="alert" className="w-full text-sm text-red-600">
          {error}
        </p>
      )}
    </form>
  )
}

function UsersList({
  users,
  onRoleChange,
  onToggleActive,
}: {
  users: AuthUser[]
  onRoleChange: (user: AuthUser, role: UserRole) => void
  onToggleActive: (user: AuthUser) => void
}) {
  if (users.length === 0) {
    return <p className="text-sm text-graphite-500">No users found.</p>
  }

  return (
    <ul className="divide-y divide-graphite-900/6">
      {users.map((user) => (
        <li key={user.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
          <span className="flex min-w-0 items-center gap-2.5">
            <StatusDot tone={user.is_active ? 'accent' : 'muted'} />
            <span className="truncate text-sm font-medium text-graphite-900">{user.username}</span>
            {user.last_login_at && (
              <span className="text-[13px] text-graphite-400">last login {formatLocalDateTime(user.last_login_at)}</span>
            )}
          </span>
          <span className="flex items-center gap-2">
            <select
              aria-label={`Role for ${user.username}`}
              value={user.role}
              onChange={(event) => onRoleChange(user, event.target.value as UserRole)}
              className="h-8 rounded-lg border border-graphite-900/10 px-2 text-xs"
            >
              {ROLE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <Button onClick={() => onToggleActive(user)}>{user.is_active ? 'Deactivate' : 'Activate'}</Button>
          </span>
        </li>
      ))}
    </ul>
  )
}

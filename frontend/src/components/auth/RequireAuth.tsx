import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../../contexts/authContextBase'
import type { UserRole } from '../../types/auth'
import { LoadingState } from '../states'

interface RequireAuthProps {
  children: ReactNode
  /** When supplied, the route additionally requires the user's role to
   * be one of these -- an authenticated user with the wrong role is
   * redirected to "/" rather than shown the page (frontend restrictions
   * are a convenience here; the backend enforces this for real on every
   * request regardless of what this component does). */
  roles?: UserRole[]
}

/**
 * Gates a route on authentication status (and, optionally, role).
 * `checking` (the brief window verifying a token that survived a page
 * refresh) renders a loading state rather than flashing the login page
 * and immediately redirecting away from it.
 */
export function RequireAuth({ children, roles }: RequireAuthProps) {
  const { status, hasRole } = useAuth()
  const location = useLocation()

  if (status === 'checking') {
    return (
      <div className="flex min-h-dvh items-center justify-center p-8">
        <LoadingState title="Checking your session…" />
      </div>
    )
  }

  if (status === 'unauthenticated') {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  if (roles && roles.length > 0 && !hasRole(...roles)) {
    return <Navigate to="/" replace />
  }

  return children
}

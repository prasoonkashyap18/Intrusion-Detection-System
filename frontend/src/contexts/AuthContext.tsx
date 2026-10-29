import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { getCurrentUser, isApiError, login as loginRequest, logout as logoutRequest } from '../services'
import { clearAuthToken, getAuthToken, setAuthToken, setUnauthorizedListener } from '../services/authToken'
import type { AuthUser, UserRole } from '../types/auth'
import { AuthContext, type AuthContextValue, type AuthStatus } from './authContextBase'

export function AuthProvider({ children }: { children: ReactNode }) {
  // No token at all means there is nothing to verify -- initialize
  // directly to "unauthenticated" rather than briefly claiming
  // "checking" and then synchronously flipping it from inside an
  // effect.
  const [status, setStatus] = useState<AuthStatus>(() => (getAuthToken() ? 'checking' : 'unauthenticated'))
  const [user, setUser] = useState<AuthUser | null>(null)

  const logout = useCallback(() => {
    clearAuthToken()
    setUser(null)
    setStatus('unauthenticated')
    // Best-effort: revoke the session server-side too, but the local
    // state above already reflects "logged out" regardless of whether
    // this succeeds (e.g. the token was already expired/revoked).
    void logoutRequest().catch(() => {})
  }, [])

  // A 401 from any request (services/api.ts) means this session's
  // token is no longer valid -- return to the logged-out state rather
  // than leaving the UI showing stale authenticated content.
  useEffect(() => {
    setUnauthorizedListener(() => {
      clearAuthToken()
      setUser(null)
      setStatus('unauthenticated')
    })
    return () => setUnauthorizedListener(null)
  }, [])

  // On mount: if a token survived a page refresh (sessionStorage), verify
  // it is still valid by asking the backend who it belongs to, rather
  // than trusting it blindly.
  useEffect(() => {
    const token = getAuthToken()
    if (!token) return
    const controller = new AbortController()
    getCurrentUser(controller.signal)
      .then((currentUser) => {
        if (controller.signal.aborted) return
        setUser(currentUser)
        setStatus('authenticated')
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        if (isApiError(error) && error.code === 'aborted') return
        clearAuthToken()
        setStatus('unauthenticated')
      })
    return () => controller.abort()
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const response = await loginRequest(username, password)
    setAuthToken(response.access_token)
    setUser(response.user)
    setStatus('authenticated')
  }, [])

  const hasRole = useCallback((...roles: UserRole[]) => user !== null && roles.includes(user.role), [user])

  const value = useMemo<AuthContextValue>(() => ({ status, user, login, logout, hasRole }), [status, user, login, logout, hasRole])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

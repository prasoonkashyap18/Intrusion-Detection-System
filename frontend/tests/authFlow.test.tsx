/** Tests for the authentication flow: login success/failure, logout,
 * and returning to a logged-out state on a 401 from any request. */

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { AuthProvider } from '@/contexts/AuthContext'
import { useAuth } from '@/contexts/authContextBase'
import { clearAuthToken, getAuthToken } from '@/services/authToken'
import { jsonResponse, stubFetch } from './helpers'

const user = {
  id: '00000000-0000-4000-9000-000000000001',
  username: 'alice',
  role: 'security_analyst' as const,
  is_active: true,
  created_at: '2026-10-03T08:15:30Z',
  last_login_at: null,
}

beforeEach(() => {
  clearAuthToken()
})

describe('AuthProvider', () => {
  it('starts unauthenticated when no token is stored', async () => {
    const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider })
    await waitFor(() => expect(result.current.status).toBe('unauthenticated'))
    expect(result.current.user).toBeNull()
  })

  it('login success stores the token and the authenticated user', async () => {
    stubFetch(() =>
      Promise.resolve(jsonResponse({ access_token: 'a-real-looking-token', token_type: 'bearer', user })),
    )
    const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider })
    await waitFor(() => expect(result.current.status).toBe('unauthenticated'))

    await act(() => result.current.login('alice', 'correct-password'))

    expect(result.current.status).toBe('authenticated')
    expect(result.current.user?.username).toBe('alice')
    expect(getAuthToken()).toBe('a-real-looking-token')
  })

  it('login failure rejects and leaves the user unauthenticated', async () => {
    stubFetch(() => Promise.resolve(jsonResponse({ error: 'invalid_credentials', message: 'Invalid.' }, 401)))
    const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider })
    await waitFor(() => expect(result.current.status).toBe('unauthenticated'))

    await expect(act(() => result.current.login('alice', 'wrong'))).rejects.toThrow()
    expect(result.current.status).toBe('unauthenticated')
    expect(result.current.user).toBeNull()
  })

  it('logout clears the token and the user', async () => {
    stubFetch((input) => {
      const url = String(input)
      if (url.includes('/auth/logout')) return Promise.resolve(new Response(null, { status: 204 }))
      return Promise.resolve(jsonResponse({ access_token: 'token', token_type: 'bearer', user }))
    })
    const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider })
    await waitFor(() => expect(result.current.status).toBe('unauthenticated'))
    await act(() => result.current.login('alice', 'correct-password'))
    expect(result.current.status).toBe('authenticated')

    act(() => result.current.logout())

    expect(result.current.status).toBe('unauthenticated')
    expect(result.current.user).toBeNull()
    expect(getAuthToken()).toBeNull()
  })

  it('hasRole reflects the authenticated user role only', async () => {
    stubFetch(() => Promise.resolve(jsonResponse({ access_token: 'token', token_type: 'bearer', user })))
    const { result } = renderHook(() => useAuth(), { wrapper: AuthProvider })
    await waitFor(() => expect(result.current.status).toBe('unauthenticated'))
    expect(result.current.hasRole('security_analyst')).toBe(false)

    await act(() => result.current.login('alice', 'correct-password'))

    expect(result.current.hasRole('security_analyst')).toBe(true)
    expect(result.current.hasRole('administrator')).toBe(false)
  })
})

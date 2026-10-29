import { LogIn } from 'lucide-react'
import { useId, useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { LogoMark } from '../../components/brand/LogoMark'
import { Button } from '../../components/ui/Button'
import { Panel } from '../../components/ui/Panel'
import { useAuth } from '../../contexts/authContextBase'
import { isApiError } from '../../services'

/** A generic message regardless of the backend's own reason (unknown
 * username, wrong password, or a disabled account) -- see
 * app.services.auth_service.login's own docstring for why those are
 * never distinguished. */
const INVALID_CREDENTIALS_MESSAGE = 'Invalid username or password.'

export function LoginPage() {
  const { status, login } = useAuth()
  const location = useLocation()
  const usernameId = useId()
  const passwordId = useId()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  if (status === 'authenticated') {
    const from = (location.state as { from?: Location } | null)?.from
    return <Navigate to={from?.pathname ?? '/'} replace />
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setIsSubmitting(true)
    try {
      await login(username, password)
    } catch (submitError) {
      setError(
        isApiError(submitError) && submitError.status === 401 ? INVALID_CREDENTIALS_MESSAGE : 'Could not sign in. Please try again.',
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="relative isolate flex min-h-dvh items-center justify-center px-5">
      <div aria-hidden="true" className="app-atmosphere" />
      <Panel className="w-full max-w-sm p-8">
        <div className="flex flex-col items-center gap-3 text-center">
          <LogoMark className="size-10" />
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-graphite-950">AI-IDS</h1>
            <p className="text-sm text-graphite-500">Sign in to Security Operations</p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor={usernameId} className="text-xs font-medium text-graphite-700">
              Username
            </label>
            <input
              id={usernameId}
              name="username"
              autoComplete="username"
              required
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              className="h-10 rounded-[10px] border border-graphite-900/10 bg-white px-3 text-sm text-graphite-950 outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/20"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor={passwordId} className="text-xs font-medium text-graphite-700">
              Password
            </label>
            <input
              id={passwordId}
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="h-10 rounded-[10px] border border-graphite-900/10 bg-white px-3 text-sm text-graphite-950 outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/20"
            />
          </div>

          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}

          <Button type="submit" variant="primary" size="md" disabled={isSubmitting} className="mt-2 w-full">
            <LogIn className="size-4" aria-hidden="true" />
            {isSubmitting ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>
      </Panel>
    </div>
  )
}

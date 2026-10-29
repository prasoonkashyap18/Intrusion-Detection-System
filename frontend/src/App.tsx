import { Navigate, Route, Routes } from 'react-router-dom'
import { RequireAuth } from './components/auth/RequireAuth'
import { AuthProvider } from './contexts/AuthContext'
import { useApiHealth } from './hooks/useApiHealth'
import { AppShell } from './layouts/AppShell'
import { AdminUsersPage } from './pages/admin-users/AdminUsersPage'
import { CommandCenterPage } from './pages/command-center/CommandCenterPage'
import { LoginPage } from './pages/login/LoginPage'
import { SocDashboardPage } from './pages/soc-dashboard/SocDashboardPage'

export function App() {
  const apiHealth = useApiHealth()

  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <AppShell activeNavId="command-center" apiHealth={apiHealth}>
                <CommandCenterPage apiHealth={apiHealth} />
              </AppShell>
            </RequireAuth>
          }
        />
        <Route
          path="/dashboard"
          element={
            <RequireAuth>
              <AppShell activeNavId="soc-dashboard" apiHealth={apiHealth}>
                <SocDashboardPage />
              </AppShell>
            </RequireAuth>
          }
        />
        <Route
          path="/admin/users"
          element={
            <RequireAuth roles={['administrator']}>
              <AppShell activeNavId="admin-users" apiHealth={apiHealth}>
                <AdminUsersPage />
              </AppShell>
            </RequireAuth>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  )
}

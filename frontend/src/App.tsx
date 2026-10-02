import { useApiHealth } from './hooks/useApiHealth'
import { AppShell } from './layouts/AppShell'
import { CommandCenterPage } from './pages/command-center/CommandCenterPage'

export function App() {
  const apiHealth = useApiHealth()

  return (
    <AppShell activeNavId="command-center" apiHealth={apiHealth}>
      <CommandCenterPage apiHealth={apiHealth} />
    </AppShell>
  )
}

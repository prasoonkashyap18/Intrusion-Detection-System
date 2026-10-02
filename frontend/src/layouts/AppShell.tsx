import { useCallback, useRef, useState, type ReactNode } from 'react'
import { CursorLight } from '../components/interaction/CursorLight'
import type { NavItemId } from '../components/navigation/navigation'
import { Sidebar } from '../components/navigation/Sidebar'
import type { ApiHealth } from '../hooks/useApiHealth'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { MobileNavDrawer } from './MobileNavDrawer'
import { TopBar } from './TopBar'

interface AppShellProps {
  activeNavId: NavItemId
  apiHealth: ApiHealth
  children: ReactNode
}

export function AppShell({ activeNavId, apiHealth, children }: AppShellProps) {
  const [isNavOpen, setIsNavOpen] = useState(false)
  const menuButtonRef = useRef<HTMLButtonElement>(null)
  const isDockedNav = useMediaQuery('(min-width: 48rem)')
  const isDrawerVisible = isNavOpen && !isDockedNav

  const openNav = useCallback(() => setIsNavOpen(true), [])
  // Native dialogs restore focus only to an element that was focused when they
  // opened; mouse clicks do not focus buttons in every browser, so do it explicitly.
  const closeNav = useCallback(() => {
    setIsNavOpen(false)
    menuButtonRef.current?.focus()
  }, [])

  return (
    <div className="relative isolate min-h-dvh">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4 focus:z-[100] focus:rounded-lg focus:bg-obsidian-700 focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-ink-50"
      >
        Skip to main content
      </a>
      <div aria-hidden="true" className="app-backdrop" />
      <CursorLight />

      <div className="md:grid md:grid-cols-[76px_minmax(0,1fr)] lg:grid-cols-[288px_minmax(0,1fr)]">
        <aside
          aria-label="Sidebar"
          className="sticky top-0 z-40 hidden h-dvh border-r border-white/6 bg-obsidian-950/40 backdrop-blur-xl md:block"
        >
          <Sidebar activeId={activeNavId} variant="docked" />
        </aside>

        <div className="flex min-h-dvh min-w-0 flex-col">
          <TopBar
            activeNavId={activeNavId}
            apiHealth={apiHealth}
            isNavOpen={isDrawerVisible}
            onOpenNav={openNav}
            menuButtonRef={menuButtonRef}
          />
          <main id="main-content" tabIndex={-1} className="flex-1 focus:outline-none">
            {children}
          </main>
        </div>
      </div>

      {isDrawerVisible && <MobileNavDrawer activeNavId={activeNavId} onClose={closeNav} />}
    </div>
  )
}

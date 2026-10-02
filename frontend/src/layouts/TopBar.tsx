import { Menu } from 'lucide-react'
import type { RefObject } from 'react'
import { findNavLocation, type NavItemId } from '../components/navigation/navigation'
import { ApiStatusBadge } from '../components/system/ApiStatusBadge'
import { UtcClock } from '../components/system/UtcClock'
import type { ApiHealth } from '../hooks/useApiHealth'

interface TopBarProps {
  activeNavId: NavItemId
  apiHealth: ApiHealth
  isNavOpen: boolean
  onOpenNav: () => void
  menuButtonRef: RefObject<HTMLButtonElement | null>
}

export function TopBar({ activeNavId, apiHealth, isNavOpen, onOpenNav, menuButtonRef }: TopBarProps) {
  const location = findNavLocation(activeNavId)

  return (
    <header className="sticky top-0 z-30 border-b border-white/6 bg-obsidian-950/60 backdrop-blur-xl">
      <div className="flex h-16 items-center gap-3 px-4 md:px-6 lg:px-8">
        <button
          ref={menuButtonRef}
          type="button"
          onClick={onOpenNav}
          aria-label="Open navigation"
          aria-expanded={isNavOpen}
          aria-controls="mobile-navigation"
          className="-ml-1 grid size-9 place-items-center rounded-lg text-ink-300 transition-colors hover:bg-white/5 hover:text-ink-50 md:hidden"
        >
          <Menu className="size-5" aria-hidden="true" />
        </button>

        {location && (
          <nav aria-label="Breadcrumb" className="min-w-0">
            <ol className="flex items-center gap-2 text-sm">
              <li className="hidden text-ink-500 sm:block">{location.section.label}</li>
              <li aria-hidden="true" className="hidden text-ink-600 sm:block">
                /
              </li>
              <li className="truncate">
                <span aria-current="page" className="font-medium text-ink-100">
                  {location.item.label}
                </span>
              </li>
            </ol>
          </nav>
        )}

        <div className="ml-auto flex items-center gap-3">
          <ApiStatusBadge health={apiHealth} />
          <span aria-hidden="true" className="hidden h-5 w-px bg-white/10 sm:block" />
          <div className="hidden sm:block">
            <UtcClock />
          </div>
        </div>
      </div>
    </header>
  )
}

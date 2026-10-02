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
    <header className="sticky top-0 z-30 border-b border-graphite-900/6 bg-canvas/75 backdrop-blur-md">
      <div className="flex h-16 items-center gap-3 px-5 md:px-8 lg:px-12">
        <button
          ref={menuButtonRef}
          type="button"
          onClick={onOpenNav}
          aria-label="Open navigation"
          aria-expanded={isNavOpen}
          aria-controls="mobile-navigation"
          className="-ml-1.5 grid size-9 place-items-center rounded-lg text-graphite-600 transition-colors hover:bg-graphite-900/5 hover:text-graphite-950 md:hidden"
        >
          <Menu className="size-5" aria-hidden="true" />
        </button>

        {location && (
          <nav aria-label="Breadcrumb" className="min-w-0">
            <ol className="flex items-center gap-2 text-sm">
              <li className="hidden text-graphite-500 sm:block">{location.section.label}</li>
              <li aria-hidden="true" className="hidden text-graphite-300 sm:block">
                /
              </li>
              <li className="truncate">
                <span aria-current="page" className="font-medium text-graphite-950">
                  {location.item.label}
                </span>
              </li>
            </ol>
          </nav>
        )}

        <div className="ml-auto flex items-center gap-4">
          <ApiStatusBadge health={apiHealth} />
          <span aria-hidden="true" className="hidden h-4 w-px bg-graphite-900/10 sm:block" />
          <div className="hidden sm:block">
            <UtcClock />
          </div>
        </div>
      </div>
    </header>
  )
}

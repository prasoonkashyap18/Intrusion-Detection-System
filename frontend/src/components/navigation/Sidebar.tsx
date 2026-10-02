import { X } from 'lucide-react'
import { cn } from '../../utils/cn'
import { LogoMark } from '../brand/LogoMark'
import { Button } from '../ui/Button'
import { NAV_SECTIONS, type NavItem, type NavItemId } from './navigation'

interface SidebarProps {
  activeId: NavItemId
  /** `docked` collapses to an icon rail between the md and lg breakpoints; `drawer` is always expanded. */
  variant: 'docked' | 'drawer'
  onClose?: () => void
}

export function Sidebar({ activeId, variant, onClose }: SidebarProps) {
  const rail = variant === 'docked'

  return (
    <div className="flex h-full flex-col px-4 pt-6 pb-5 lg:px-5">
      <div className={cn('flex items-center justify-between gap-2 px-1', rail && 'md:max-lg:justify-center md:max-lg:px-0')}>
        <a href="/" className="flex items-center gap-3 rounded-xl" aria-label="AI-IDS home">
          <LogoMark className="size-9 shrink-0" />
          <span className={cn('flex flex-col', rail && 'md:max-lg:hidden')}>
            <span className="text-[15px] leading-tight font-semibold tracking-tight text-graphite-950">AI-IDS</span>
            <span className="text-xs text-graphite-500">Security Operations</span>
          </span>
        </a>
        {variant === 'drawer' && (
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close navigation">
            <X className="size-[18px]" aria-hidden="true" />
          </Button>
        )}
      </div>

      <nav aria-label="Primary" className="mt-10 flex-1">
        {NAV_SECTIONS.map((section) => {
          const headingId = `nav-${variant}-${section.id}`
          const sectionPlanned = section.items.every((item) => !item.href)
          return (
            <div key={section.id} className="mb-8 last:mb-0">
              <div className={cn('mb-2.5 flex items-center justify-between px-3', rail && 'md:max-lg:hidden')}>
                <p id={headingId} className="font-mono text-micro uppercase tracking-label text-graphite-500">
                  {section.label}
                </p>
                {sectionPlanned && (
                  <span className="rounded-full px-1.5 py-px text-[11px] text-graphite-500 ring-1 ring-graphite-900/8">
                    Planned
                  </span>
                )}
              </div>
              {rail && <span aria-hidden="true" className="mx-auto mb-3 hidden h-px w-6 bg-graphite-200 md:max-lg:block" />}
              <ul aria-labelledby={headingId} className="flex flex-col gap-1">
                {section.items.map((item) => (
                  <li key={item.id}>
                    <NavEntry
                      item={item}
                      active={item.id === activeId}
                      rail={rail}
                      showPlannedBadge={!sectionPlanned && !item.href}
                    />
                  </li>
                ))}
              </ul>
            </div>
          )
        })}
      </nav>

      <div className={cn('border-t border-graphite-900/6 px-3 pt-4', rail && 'md:max-lg:hidden')}>
        <p className="flex items-center justify-between text-xs text-graphite-500">
          <span>
            Build <span className="font-mono tabular-nums text-graphite-700">v{import.meta.env.VITE_APP_VERSION}</span>
          </span>
          <span className="font-mono">{import.meta.env.MODE}</span>
        </p>
      </div>
    </div>
  )
}

interface NavEntryProps {
  item: NavItem
  active: boolean
  rail: boolean
  showPlannedBadge: boolean
}

function NavEntry({ item, active, rail, showPlannedBadge }: NavEntryProps) {
  const Icon = item.icon
  const labelClass = rail ? 'md:max-lg:sr-only' : undefined
  const layout = cn('group relative flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-sm', rail && 'md:max-lg:justify-center')

  if (!item.href) {
    return (
      <div className={cn(layout, 'text-graphite-400')}>
        <Icon className="size-[18px] shrink-0 text-graphite-300" strokeWidth={1.6} aria-hidden="true" />
        <span className={cn('flex-1 truncate', labelClass)}>{item.label}</span>
        <span className="sr-only">(planned)</span>
        {showPlannedBadge && (
          <span aria-hidden="true" className="rounded-full px-1.5 py-px text-[11px] text-graphite-500 ring-1 ring-graphite-900/8">
            Planned
          </span>
        )}
        {rail && <RailTooltip label={`${item.label} · Planned`} />}
      </div>
    )
  }

  return (
    <a
      href={item.href}
      aria-current={active ? 'page' : undefined}
      className={cn(
        layout,
        'font-medium transition-[color,background-color,box-shadow] duration-200',
        active
          ? 'bg-white text-graphite-950 shadow-control ring-1 ring-graphite-900/7'
          : 'text-graphite-600 hover:bg-graphite-900/4 hover:text-graphite-950',
      )}
    >
      {active && (
        <span aria-hidden="true" className="absolute top-1/2 left-0 h-4 w-[3px] -translate-y-1/2 rounded-r-full bg-accent-500" />
      )}
      <Icon
        className={cn(
          'size-[18px] shrink-0 transition-colors duration-200',
          active ? 'text-accent-600' : 'text-graphite-400 group-hover:text-graphite-700',
        )}
        strokeWidth={1.6}
        aria-hidden="true"
      />
      <span className={cn('truncate', labelClass)}>{item.label}</span>
      {rail && <RailTooltip label={item.label} />}
    </a>
  )
}

function RailTooltip({ label }: { label: string }) {
  return (
    <span
      aria-hidden="true"
      className="pointer-events-none absolute top-1/2 left-full z-50 ml-3 hidden -translate-y-1/2 rounded-md bg-graphite-900 px-2 py-1 text-xs whitespace-nowrap text-white opacity-0 shadow-raised transition-opacity duration-150 group-hover:opacity-100 group-focus-visible:opacity-100 md:max-lg:block"
    >
      {label}
    </span>
  )
}

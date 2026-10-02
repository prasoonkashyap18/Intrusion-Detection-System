import { X } from 'lucide-react'
import { cn } from '../../utils/cn'
import { LogoMark } from '../brand/LogoMark'
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
    <div className="flex h-full flex-col gap-7 px-3 py-5 lg:px-4">
      <div className={cn('flex items-center justify-between gap-2 px-2', rail && 'md:max-lg:justify-center md:max-lg:px-0')}>
        <a href="/" className="flex items-center gap-3 rounded-lg" aria-label="AI-IDS home">
          <LogoMark className="size-8 shrink-0" />
          <span className={cn('flex flex-col leading-none', rail && 'md:max-lg:hidden')}>
            <span className="text-[15px] font-semibold tracking-tight text-ink-50">AI-IDS</span>
            <span className="mt-1.5 font-mono text-[10px] uppercase tracking-[0.22em] text-ink-500">
              Security Ops
            </span>
          </span>
        </a>
        {variant === 'drawer' && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close navigation"
            className="grid size-9 place-items-center rounded-lg text-ink-300 transition-colors hover:bg-white/5 hover:text-ink-50"
          >
            <X className="size-5" aria-hidden="true" />
          </button>
        )}
      </div>

      <nav aria-label="Primary" className="flex-1">
        {NAV_SECTIONS.map((section) => {
          const headingId = `nav-${variant}-${section.id}`
          return (
            <div key={section.id} className="mb-6">
              <p
                id={headingId}
                className={cn(
                  'mb-2 px-3 font-mono text-micro uppercase tracking-label text-ink-500',
                  rail && 'md:max-lg:sr-only',
                )}
              >
                {section.label}
              </p>
              <ul aria-labelledby={headingId} className="flex flex-col gap-0.5">
                {section.items.map((item) => (
                  <li key={item.id}>
                    <NavEntry item={item} active={item.id === activeId} rail={rail} />
                  </li>
                ))}
              </ul>
            </div>
          )
        })}
      </nav>

      <div className={cn('rounded-xl border border-white/6 bg-white/2 px-3 py-2.5', rail && 'md:max-lg:hidden')}>
        <p className="font-mono text-micro uppercase tracking-label text-ink-500">Build</p>
        <p className="mt-1 flex items-center justify-between text-xs text-ink-300">
          <span className="font-mono tabular-nums">v{import.meta.env.VITE_APP_VERSION}</span>
          <span className="font-mono text-ink-500">{import.meta.env.MODE}</span>
        </p>
      </div>
    </div>
  )
}

function NavEntry({ item, active, rail }: { item: NavItem; active: boolean; rail: boolean }) {
  const Icon = item.icon
  const labelClass = rail ? 'md:max-lg:sr-only' : undefined

  if (!item.href) {
    return (
      <div
        className={cn(
          'group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-ink-500',
          rail && 'md:max-lg:justify-center',
        )}
      >
        <Icon className="size-[18px] shrink-0 text-ink-600" strokeWidth={1.75} aria-hidden="true" />
        <span className={cn('flex-1 truncate', labelClass)}>{item.label}</span>
        <span className="sr-only">(planned)</span>
        <span
          aria-hidden="true"
          className={cn(
            'rounded-full border border-white/8 px-1.5 py-px font-mono text-[10px] uppercase tracking-wider text-ink-500',
            rail && 'md:max-lg:hidden',
          )}
        >
          Planned
        </span>
        {rail && <RailTooltip label={`${item.label} · Planned`} />}
      </div>
    )
  }

  return (
    <a
      href={item.href}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-200',
        rail && 'md:max-lg:justify-center',
        active ? 'bg-white/5 text-ink-50' : 'text-ink-300 hover:bg-white/3 hover:text-ink-100',
      )}
    >
      {active && (
        <span aria-hidden="true" className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-accent shadow-glow" />
      )}
      <Icon
        className={cn(
          'size-[18px] shrink-0 transition-colors',
          active ? 'text-accent' : 'text-ink-400 group-hover:text-ink-200',
        )}
        strokeWidth={1.75}
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
      className="pointer-events-none absolute top-1/2 left-full z-50 ml-3 hidden -translate-y-1/2 rounded-md border border-white/10 bg-obsidian-700 px-2 py-1 text-xs whitespace-nowrap text-ink-100 opacity-0 shadow-panel transition-opacity duration-150 group-hover:opacity-100 group-focus-visible:opacity-100 md:max-lg:block"
    >
      {label}
    </span>
  )
}

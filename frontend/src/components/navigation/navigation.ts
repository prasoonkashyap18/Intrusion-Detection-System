import { ChartColumn, FileUp, Gauge, LayoutDashboard, ListChecks, ShieldAlert, Users, type LucideIcon } from 'lucide-react'

export type NavItemId =
  | 'command-center'
  | 'soc-dashboard'
  | 'ingest'
  | 'detections'
  | 'analytics'
  | 'model-performance'
  | 'admin-users'

export interface NavItem {
  id: NavItemId
  label: string
  icon: LucideIcon
  /** Only destinations that exist have an href; the rest are shown as planned. */
  href?: string
}

export interface NavSection {
  id: string
  label: string
  items: NavItem[]
}

export const NAV_SECTIONS: NavSection[] = [
  {
    id: 'operations',
    label: 'Operations',
    items: [
      { id: 'command-center', label: 'Command Center', icon: LayoutDashboard, href: '/' },
      { id: 'soc-dashboard', label: 'SOC Dashboard', icon: ShieldAlert, href: '/dashboard' },
    ],
  },
  {
    id: 'detection',
    label: 'Detection',
    items: [
      { id: 'ingest', label: 'Upload & Analyze', icon: FileUp },
      { id: 'detections', label: 'Detection Results', icon: ListChecks },
    ],
  },
  {
    id: 'insights',
    label: 'Insights',
    items: [
      { id: 'analytics', label: 'Analytics', icon: ChartColumn },
      { id: 'model-performance', label: 'Model Performance', icon: Gauge },
    ],
  },
]

/** Shown only to an authenticated Administrator — see Sidebar.tsx,
 * which filters this in based on the current user's role rather than
 * this module (which has no access to auth state) deciding anything. */
export const ADMIN_NAV_ITEM: NavItem = { id: 'admin-users', label: 'User Management', icon: Users, href: '/admin/users' }

export function findNavLocation(id: NavItemId): { section: NavSection; item: NavItem } | null {
  for (const section of NAV_SECTIONS) {
    const item = section.items.find((candidate) => candidate.id === id)
    if (item) return { section, item }
  }
  return null
}

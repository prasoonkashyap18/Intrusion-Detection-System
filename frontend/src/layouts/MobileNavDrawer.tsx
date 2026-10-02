import { useCallback, useEffect, useRef } from 'react'
import type { NavItemId } from '../components/navigation/navigation'
import { Sidebar } from '../components/navigation/Sidebar'

interface MobileNavDrawerProps {
  activeNavId: NavItemId
  /** Called after the dialog has closed (via Escape, backdrop click, or the close button). */
  onClose: () => void
}

/**
 * Small-screen navigation as a native modal <dialog>: the browser provides
 * focus containment, Escape and backdrop light-dismiss (`closedby="any"`),
 * background inertness, and returns focus to the menu button on close.
 */
export function MobileNavDrawer({ activeNavId, onClose }: MobileNavDrawerProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return undefined

    dialog.addEventListener('close', onClose)
    if (!dialog.open) dialog.showModal()
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    // No dialog.close() here: its `close` event is dispatched asynchronously and
    // would reach the listener of a re-run effect. Unmounting removes the dialog
    // from the top layer anyway.
    return () => {
      dialog.removeEventListener('close', onClose)
      document.body.style.overflow = previousOverflow
    }
  }, [onClose])

  const closeDialog = useCallback(() => dialogRef.current?.close(), [])

  return (
    <dialog
      id="mobile-navigation"
      ref={dialogRef}
      aria-label="Navigation"
      closedby="any"
      className="fixed inset-y-0 left-0 m-0 h-dvh max-h-none w-72 max-w-[85vw] border-r border-white/8 bg-obsidian-900 p-0 text-ink-200 shadow-panel backdrop:bg-obsidian-950/70 backdrop:backdrop-blur-sm"
    >
      <Sidebar activeId={activeNavId} variant="drawer" onClose={closeDialog} />
    </dialog>
  )
}

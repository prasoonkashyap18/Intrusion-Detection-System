import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'
import { stubMatchMedia } from './helpers'

// Local-time formatting is asserted exactly, so pin the timezone.
process.env.TZ = 'UTC'

// jsdom does not implement <dialog>'s showModal()/close() (only the `open`
// property). Components (BatchDetailDialog, MobileNavDrawer) rely on both,
// so provide a minimal, spec-shaped polyfill: `close` sets `open = false`
// and fires a non-bubbling `close` event, matching what those components'
// own `dialog.addEventListener('close', ...)` listeners expect.
if (typeof HTMLDialogElement !== 'undefined' && !HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute('open', '')
  }
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement, returnValue?: string) {
    if (!this.open) return
    if (returnValue !== undefined) this.returnValue = returnValue
    this.removeAttribute('open')
    this.dispatchEvent(new Event('close'))
  }
}

// Tests import Vitest helpers explicitly (globals are off), so React Testing
// Library's automatic cleanup is not registered for us.
afterEach(cleanup)

// Default environment: a pointer-less jsdom with motion allowed. Tests that
// care about reduced motion stub it again.
beforeEach(() => stubMatchMedia(false))

/**
 * Tests for the reusable UI state components: what each one renders, how the
 * optional actions behave, and the accessibility affordances the UI relies on.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Inbox } from 'lucide-react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { EmptyState, ErrorState, LoadingState, OfflineState } from '@/components/states'

/** jsdom has no matchMedia; the components query it for reduced-motion. */
function stubReducedMotion(prefersReduced: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: query.includes('prefers-reduced-motion') ? prefersReduced : false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  )
}

beforeEach(() => stubReducedMotion(false))

describe('LoadingState', () => {
  it('renders a default message and marks itself busy in a live region', () => {
    render(<LoadingState />)

    // A live region is announced from its content, so the message lives inside it.
    const status = screen.getByRole('status')
    expect(status).toHaveAttribute('aria-busy', 'true')
    expect(status).toHaveAttribute('aria-live', 'polite')
    expect(status).toHaveTextContent('Loading')
  })

  it('renders the supplied title and description', () => {
    render(<LoadingState title="Connecting to AI-IDS API" description="This usually takes a moment." />)

    expect(screen.getByText('Connecting to AI-IDS API')).toBeInTheDocument()
    expect(screen.getByText('This usually takes a moment.')).toBeInTheDocument()
  })

  it('animates the indicator by default', () => {
    render(<LoadingState />)

    const spinner = screen.getByTestId('loading-spinner')
    expect(spinner.getAttribute('class')).toContain('animate-spin')
    expect(spinner).not.toHaveAttribute('data-static')
  })

  it('renders a static indicator when the user prefers reduced motion', () => {
    stubReducedMotion(true)

    render(<LoadingState />)

    const spinner = screen.getByTestId('loading-spinner')
    expect(spinner.getAttribute('class')).not.toContain('animate-spin')
    expect(spinner).toHaveAttribute('data-static')
    // The message still conveys the state without motion.
    expect(screen.getByText('Loading')).toBeInTheDocument()
  })

  it('supports a compact layout', () => {
    const { container } = render(<LoadingState compact title="Saving" />)

    expect(screen.getByText('Saving')).toBeInTheDocument()
    expect(container.querySelector('output')?.className).toContain('items-center')
  })
})

describe('ErrorState', () => {
  it('renders the supplied message under a default title', () => {
    render(<ErrorState message="The backend could not be reached." />)

    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
    expect(screen.getByText('The backend could not be reached.')).toBeInTheDocument()
  })

  it('announces itself as an alert', () => {
    render(<ErrorState title="Unable to load detection data" message="Please try again." />)

    expect(screen.getByRole('alert')).toHaveTextContent('Unable to load detection data')
  })

  it('omits the retry button when no callback is supplied', () => {
    render(<ErrorState message="Please try again." />)

    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('invokes the retry callback when the button is activated', async () => {
    const onRetry = vi.fn()
    const user = userEvent.setup()
    render(<ErrorState message="Please try again." onRetry={onRetry} />)

    await user.click(screen.getByRole('button', { name: 'Retry' }))

    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('reaches the retry button by keyboard and activates it with Enter', async () => {
    const onRetry = vi.fn()
    const user = userEvent.setup()
    render(<ErrorState message="Please try again." onRetry={onRetry} />)

    await user.tab()

    expect(screen.getByRole('button', { name: 'Retry' })).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onRetry).toHaveBeenCalledOnce()
  })
})

describe('EmptyState', () => {
  it('explains the absence rather than rendering a bare space', () => {
    render(
      <EmptyState
        icon={Inbox}
        title="No detection data yet"
        description="Upload network traffic to begin analysis."
      />,
    )

    expect(screen.getByText('No detection data yet')).toBeInTheDocument()
    expect(screen.getByText('Upload network traffic to begin analysis.')).toBeInTheDocument()
  })

  it('is not announced as an alert, because nothing has failed', () => {
    render(<EmptyState icon={Inbox} title="No batches available" description="Nothing has been uploaded." />)

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('renders an optional action and invokes it', async () => {
    const onAction = vi.fn()
    const user = userEvent.setup()
    render(
      <EmptyState
        icon={Inbox}
        title="No batches available"
        description="Nothing has been uploaded."
        action={
          <button type="button" onClick={onAction}>
            Upload CSV
          </button>
        }
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Upload CSV' }))

    expect(onAction).toHaveBeenCalledOnce()
  })
})

describe('OfflineState', () => {
  it('renders a default explanation of the unavailable backend', () => {
    render(<OfflineState />)

    expect(screen.getByRole('alert')).toHaveTextContent('AI-IDS backend unavailable')
    expect(screen.getByText(/FastAPI service is running/)).toBeInTheDocument()
  })

  it('invokes the retry callback when the button is activated', async () => {
    const onRetry = vi.fn()
    const user = userEvent.setup()
    render(<OfflineState onRetry={onRetry} />)

    await user.click(screen.getByRole('button', { name: 'Retry connection' }))

    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('disables the retry button while a check is already running', async () => {
    const onRetry = vi.fn()
    // The disabled button also sets pointer-events: none, which user-event
    // would otherwise reject before attempting the click.
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    render(<OfflineState onRetry={onRetry} isRetrying />)

    const button = screen.getByRole('button', { name: 'Reconnecting…' })
    expect(button).toBeDisabled()

    await user.click(button)
    expect(onRetry).not.toHaveBeenCalled()
  })

  it('omits the retry button when no callback is supplied', () => {
    render(<OfflineState />)

    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})

describe('state components as a set', () => {
  it('never communicates meaning through colour alone', () => {
    // Each state carries a text title, so the meaning survives without colour
    // perception, a monochrome display, or a screen reader.
    const states = [
      render(<LoadingState title="Loading detection data" />),
      render(<ErrorState message="The backend could not be reached." />),
      render(<EmptyState icon={Inbox} title="No detection data yet" description="Nothing to show." />),
      render(<OfflineState />),
    ]

    for (const { container } of states) {
      expect(container.textContent?.trim()).not.toBe('')
    }
  })
})

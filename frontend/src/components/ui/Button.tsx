import { useRef, type ComponentPropsWithoutRef } from 'react'
import { useMagnetic } from '../../hooks/useMagnetic'
import { cn } from '../../utils/cn'

type ButtonVariant = 'secondary' | 'ghost'
type ButtonSize = 'sm' | 'icon'

const BASE =
  'inline-flex shrink-0 items-center justify-center font-medium transition-[color,background-color,box-shadow] duration-150 ease-out active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50'

const VARIANTS: Record<ButtonVariant, string> = {
  secondary:
    'bg-white text-graphite-800 shadow-control ring-1 ring-graphite-900/8 hover:text-graphite-950 hover:ring-graphite-900/15 active:bg-graphite-50',
  ghost: 'text-graphite-600 hover:bg-graphite-900/5 hover:text-graphite-950 active:bg-graphite-900/8',
}

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 gap-1.5 rounded-lg px-3 text-xs',
  icon: 'size-8 rounded-lg',
}

interface ButtonProps extends ComponentPropsWithoutRef<'button'> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Drifts subtly toward the cursor on hover. Reserve for a few primary controls. */
  magnetic?: boolean
}

export function Button({
  variant = 'secondary',
  size = 'sm',
  magnetic = false,
  className,
  type = 'button',
  ...rest
}: ButtonProps) {
  const ref = useRef<HTMLButtonElement>(null)
  useMagnetic(ref, magnetic)

  return (
    <button
      ref={ref}
      type={type}
      className={cn(BASE, VARIANTS[variant], SIZES[size], magnetic && 'magnetic', className)}
      {...rest}
    />
  )
}

import { useId } from 'react'

export function LogoMark({ className }: { className?: string }) {
  const gradientId = useId()

  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden="true" className={className}>
      <defs>
        <linearGradient id={gradientId} x1="4" y1="2" x2="28" y2="30" gradientUnits="userSpaceOnUse">
          <stop style={{ stopColor: 'var(--color-accent)' }} />
          <stop offset="1" style={{ stopColor: 'var(--color-ice)' }} />
        </linearGradient>
      </defs>
      <path
        d="M16 3.5 27 9.75v12.5L16 28.5 5 22.25V9.75L16 3.5Z"
        stroke={`url(#${gradientId})`}
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path
        d="M16 10 21.25 13v6L16 22l-5.25-3v-6L16 10Z"
        className="fill-accent/15"
        stroke={`url(#${gradientId})`}
        strokeWidth="1.25"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="16" r="2" className="fill-accent" />
    </svg>
  )
}

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden="true" className={className}>
      <rect width="32" height="32" rx="9" className="fill-graphite-900" />
      <path
        d="M16 5 25.5 10.5v11L16 27l-9.5-5.5v-11L16 5Z"
        className="stroke-graphite-100"
        strokeOpacity="0.9"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path
        d="M16 10.75 20.5 13.375v5.25L16 21.25l-4.5-2.625v-5.25L16 10.75Z"
        className="fill-accent-500/20 stroke-accent-400"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="16" r="1.9" className="fill-accent-400" />
    </svg>
  )
}

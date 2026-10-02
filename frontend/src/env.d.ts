interface ImportMetaEnv {
  /** Public, browser-visible base URL of the AI-IDS backend. Never put secrets in VITE_* variables. */
  readonly VITE_API_BASE_URL?: string
  /** Injected at build time from package.json (see vite.config.ts). */
  readonly VITE_APP_VERSION: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

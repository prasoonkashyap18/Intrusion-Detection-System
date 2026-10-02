import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    'import.meta.env.VITE_APP_VERSION': JSON.stringify(process.env.npm_package_version ?? '0.0.0'),
  },
  server: {
    port: 5173,
    // The backend's default CORS allow-list contains this exact origin, so
    // fail fast instead of silently moving to a port the API would reject.
    strictPort: true,
  },
})

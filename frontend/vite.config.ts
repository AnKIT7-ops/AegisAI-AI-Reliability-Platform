import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The dev server talks to the running stack through nginx.
    proxy: { '/api': 'http://127.0.0.1:8080' },
  },
})

import process from 'node:process'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: process.env.BODEGA_ALLOWED_HOSTS?.split(',') || [],
    proxy: { '/api': process.env.BODEGA_API_TARGET || 'http://localhost:8000' },
  },
})

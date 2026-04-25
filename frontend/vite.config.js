import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/orders':    'http://localhost:8002',
      '/cache':     'http://localhost:8002',
      '/analytics': 'http://localhost:8002',
      '/customers': 'http://localhost:8002',
      '/inventory': 'http://localhost:8002',
      '/health':    'http://localhost:8002',
    },
  },
})

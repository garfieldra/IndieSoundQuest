import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  return {
    plugins: [react()],
    server: {
      host: '0.0.0.0',
      port: 5173,
      proxy: {
        '/api': {
          // Docker resolves the Java service by Compose name; local Vite can
          // override this with VITE_API_PROXY_TARGET=http://localhost:8080.
          target: env.VITE_API_PROXY_TARGET ?? 'http://java-service:8080',
          changeOrigin: true,
        },
      },
    },
  }
})

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        // Perf (2026-07-11): split long-lived vendor code into its own
        // cacheable chunks. The graph stack (@xyflow/react + d3-force)
        // is NOT listed here - it ships in the lazy chunks created by
        // the React.lazy() imports of the three graph panels.
        manualChunks: {
          'vendor-react': ['react', 'react-dom'],
          'vendor-markdown': ['react-markdown', 'remark-gfm'],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        // VITE_API_TARGET lets dev hosts point at a non-default backend port
        // (e.g. when the installed Hub is already bound to 8003 and we
        // run a side-by-side dev backend on 8014). Defaults to the
        // installed Hub's port for the typical "git pull, npm run dev"
        // workflow where Tory is iterating against the running install.
        target: process.env.VITE_API_TARGET || 'http://localhost:8003',
        changeOrigin: true,
      },
    },
  },
})

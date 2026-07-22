import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'

// One query cache for the whole app. Server state (Pi status, overseer
// data, ...) belongs here, not in per-component useState + setInterval.
// Defaults: a request shared by two components within 15s dedupes to
// one network call; failed requests retry once.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: 1,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
)

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { retryPolicy } from './lib/retry'
import './index.css'

// The portfolio changes when someone edits it, not on a timer, so refetching on
// every window focus produced a request storm during a workshop with the app on a
// second monitor. A 30s stale window covers the case that matters: a mutation
// invalidates its keys explicitly.
//
// `retry` is set for the reason TIER3_MIGRATION_PLAN.md §4.4 gives, though not in
// the place it points at. Mutations already default to `retry: 0` in react-query v5
// (`query-core/mutation.js:83`), so a per-mutation `retry: false` changes nothing.
// QUERIES default to `retry: 3` with exponential backoff — so before this, every
// GET that hit a 429 fired three more requests at an endpoint that had just asked
// us to wait. `retryPolicy` retries transient failures and never a deliberate 4xx.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, staleTime: 30_000, retry: retryPolicy },
  },
})

const container = document.getElementById('root')
if (!container) throw new Error('#root is missing from index.html')

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
